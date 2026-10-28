"""Real row-lock regressions; runnable with unittest in an isolated runtime."""

import asyncio
import os
import threading
import time
import unittest
from contextlib import suppress
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import BackgroundTasks, Request
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ReplicaDocumentRecord, UserRecord, WorkspaceRecord, utc_now
from app.replica import (
    _analysis_heartbeat,
    _touch_analysis_heartbeat,
    creation_records,
    get_document,
)


class ReplicaHeartbeatPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw_url = os.environ.get("POLARIS_TEST_DATABASE_URL")
        if not raw_url:
            raise unittest.SkipTest("POLARIS_TEST_DATABASE_URL is required")
        url = make_url(raw_url)
        if url.get_backend_name() != "postgresql":
            raise RuntimeError("Use an isolated PostgreSQL test database")
        cls.schema = "replica_heartbeat_" + uuid4().hex
        cls.admin = create_engine(url)
        with cls.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        options = f"{url.query.get('options', '')} -csearch_path={cls.schema}"
        cls.engine = create_engine(url.update_query_dict({"options": options}))
        cls.factory = sessionmaker(cls.engine, expire_on_commit=False)
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin.dispose()

    def setUp(self):
        suffix = uuid4().hex
        self.user_id = "usr_" + suffix
        self.workspace_id = "ws_" + suffix
        self.document_id = "rep_" + suffix
        self.attempt_id = uuid4().hex
        self.original_time = utc_now() - timedelta(days=1)
        with self.factory() as session:
            session.add(
                UserRecord(id=self.user_id, auth_provider="test", auth_subject=suffix)
            )
            session.flush()
            session.add(
                WorkspaceRecord(
                    id=self.workspace_id,
                    name="Synthetic lock regression",
                    created_by_user_id=self.user_id,
                )
            )
            session.flush()
            session.add(
                ReplicaDocumentRecord(
                    id=self.document_id,
                    workspace_id=self.workspace_id,
                    created_by=self.user_id,
                    kind="session",
                    fingerprint=suffix * 2,
                    status="analysing",
                    payload={
                        "analysis_attempt_id": self.attempt_id,
                        "job_ids": [],
                        "plans": [],
                    },
                    updated_at=self.original_time,
                )
            )
            session.commit()
        self.app = SimpleNamespace(state=SimpleNamespace(session_factory=self.factory))

    def tearDown(self):
        with self.factory() as session:
            session.execute(
                delete(WorkspaceRecord).where(WorkspaceRecord.id == self.workspace_id)
            )
            session.execute(delete(UserRecord).where(UserRecord.id == self.user_id))
            session.commit()

    def test_creation_history_unpacks_once_and_invalidates_versions(self):
        with self.factory() as session:
            row = session.get(ReplicaDocumentRecord, self.document_id)
            row.payload = {
                **row.payload,
                "replication_method": "style",
                "progress": 42,
                "analysis_progress": {"total": 12},
                "_analysis_checkpoint": {"private": "PRIVATE_CHECKPOINT" * 100000},
            }
            session.commit()
        workspace = SimpleNamespace(workspace_id=self.workspace_id)
        with self.factory() as session:
            first = creation_records(workspace, session)
        self.assertEqual(first[0]["expected"], 12)
        self.assertEqual(first[0]["progress"], 42)
        self.assertNotIn("PRIVATE_CHECKPOINT", str(first))
        statements = []

        def observe(_connection, _cursor, sql, _parameters, _context, _many):
            statements.append(sql)

        event.listen(self.engine, "before_cursor_execute", observe)
        try:
            with self.factory() as session:
                repeated = creation_records(workspace, session)
        finally:
            event.remove(self.engine, "before_cursor_execute", observe)
        self.assertEqual(first, repeated)
        self.assertFalse(any("jsonb_to_record" in sql for sql in statements))
        with self.factory() as session:
            row = session.get(ReplicaDocumentRecord, self.document_id)
            row.payload = {**row.payload, "progress": 73}
            row.updated_at = utc_now()
            session.commit()
        with self.factory() as session:
            self.assertEqual(creation_records(workspace, session)[0]["progress"], 73)
            self.assertEqual(
                creation_records(SimpleNamespace(workspace_id="ws_missing"), session),
                [],
            )
            row = session.get(ReplicaDocumentRecord, self.document_id)
            row.updated_at = utc_now() - timedelta(days=31)
            session.commit()
        with self.factory() as session:
            self.assertEqual(creation_records(workspace, session), [])

    def test_poll_releases_row_lock_before_dependency_scope_ends(self):
        with self.factory() as reader:
            # Retain the dependency session after the handler has returned,
            # as happens while FastAPI sends a response/background work runs.
            row = reader.get(ReplicaDocumentRecord, self.document_id)
            row.updated_at = utc_now()
            reader.commit()
            response = get_document(
                self.document_id,
                Request({"type": "http", "app": self.app}),
                BackgroundTasks(),
                SimpleNamespace(workspace_id=self.workspace_id),
                reader,
            )
            self.assertEqual(response["status"], "analysing")
            self.assertFalse(reader.in_transaction())
            # An independent update must succeed while reader remains open.
            self.assertTrue(
                _touch_analysis_heartbeat(self.app, self.document_id, self.attempt_id)
            )

    def test_locked_heartbeat_keeps_event_loop_responsive_and_recovers(self):
        locked, release = threading.Event(), threading.Event()
        failures = []

        def hold_row_lock():
            try:
                with self.factory() as session:
                    session.scalar(
                        select(ReplicaDocumentRecord)
                        .where(ReplicaDocumentRecord.id == self.document_id)
                        .with_for_update()
                    )
                    locked.set()
                    # A watchdog releases even a regressed, blocked event loop.
                    release.wait(2)
            except Exception as error:
                failures.append(error)
                locked.set()

        holder = threading.Thread(target=hold_row_lock, daemon=True)
        holder.start()
        self.assertTrue(locked.wait(3))
        self.assertFalse(failures)

        def modified_at():
            with self.factory() as session:
                return session.get(ReplicaDocumentRecord, self.document_id).updated_at

        async def scenario():
            pulse = asyncio.create_task(
                _analysis_heartbeat(
                    self.app, self.document_id, self.attempt_id, interval=0.01
                )
            )
            try:
                started = time.monotonic()
                await asyncio.sleep(0.06)
                self.assertLess(time.monotonic() - started, 0.4)
                await asyncio.sleep(0.3)  # The first bounded lock wait expires.
                self.assertFalse(pulse.done())
                self.assertEqual(
                    await asyncio.to_thread(modified_at), self.original_time
                )
                release.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if await asyncio.to_thread(modified_at) > self.original_time:
                        return
                    await asyncio.sleep(0.02)
                self.fail("Heartbeat did not recover after the row lock was released")
            finally:
                release.set()
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse

        try:
            asyncio.run(scenario())
        finally:
            release.set()
            holder.join(3)
        self.assertFalse(holder.is_alive())
        self.assertFalse(failures)

    def test_heartbeat_does_not_touch_replaced_attempt_or_terminal_document(self):
        with self.factory() as session:
            row = session.get(ReplicaDocumentRecord, self.document_id)
            row.payload = {**row.payload, "analysis_attempt_id": "replacement"}
            session.commit()
            replaced_at = row.updated_at
        self.assertFalse(
            _touch_analysis_heartbeat(self.app, self.document_id, self.attempt_id)
        )
        with self.factory() as session:
            row = session.get(ReplicaDocumentRecord, self.document_id)
            self.assertEqual(row.updated_at, replaced_at)
            row.status = "completed"
            session.commit()
            completed_at = row.updated_at
        self.assertFalse(
            _touch_analysis_heartbeat(self.app, self.document_id, "replacement")
        )
        with self.factory() as session:
            self.assertEqual(
                session.get(ReplicaDocumentRecord, self.document_id).updated_at,
                completed_at,
            )


if __name__ == "__main__":
    unittest.main()
