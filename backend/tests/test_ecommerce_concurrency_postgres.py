from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.ecommerce_crawl_tasks import _acquire_profile_lease, _select_automatic_account
from app.models import (
    CrawlTaskRecord,
    EcommerceAccountRecord,
    UserRecord,
    WorkspaceRecord,
    utc_now,
)


@pytest.fixture
def ecommerce_postgres(tmp_path):
    raw_url = os.environ.get("POLARIS_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("set POLARIS_TEST_DATABASE_URL for PostgreSQL concurrency tests")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError("POLARIS_TEST_DATABASE_URL must use PostgreSQL")
    schema = f"ecommerce_concurrency_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    options = (
        f"{url.query.get('options', '')} -csearch_path={schema} -clock_timeout=5000"
    ).strip()
    engine = create_engine(url.update_query_dict({"options": options}))
    factory = sessionmaker(engine)
    try:
        Base.metadata.create_all(engine)
        with factory() as session:
            session.add(
                UserRecord(
                    id="usr_parallel", auth_provider="test", auth_subject="parallel"
                )
            )
            session.flush()
            session.add(
                WorkspaceRecord(
                    id="ws_parallel",
                    name="Concurrent accounts",
                    created_by_user_id="usr_parallel",
                )
            )
            session.commit()
            for number in range(2):
                session.add(
                    EcommerceAccountRecord(
                        id=f"account_{number}",
                        workspace_id="ws_parallel",
                        created_by="usr_parallel",
                        platform="jd",
                        account_key=f"account-{number}",
                        profile_id=f"profile-{number}",
                        profile_path=str(tmp_path / f"account-{number}"),
                        login_state="ready",
                        status="active",
                        created_at=utc_now() + timedelta(seconds=number),
                    )
                )
            session.add(
                CrawlTaskRecord(
                    id="task_parallel",
                    workspace_id="ws_parallel",
                    created_by="usr_parallel",
                    platform="jd",
                    source_url="https://item.jd.com/10031245841078.html",
                    canonical_url="https://item.jd.com/10031245841078.html",
                    request_hash="a" * 64,
                    status="queued",
                    profile_id="profile-0",
                    request_payload={},
                )
            )
            session.commit()
        yield factory
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_simultaneous_assignments_use_two_ready_accounts(ecommerce_postgres):
    barrier = threading.Barrier(2)

    def assign():
        barrier.wait(timeout=5)
        with ecommerce_postgres() as session:
            account, configured = _select_automatic_account(
                session, workspace_id="ws_parallel", platform="jd", lock_accounts=True
            )
            assert configured and account is not None
            selected = account.id
            # Keep the first transaction open so the second must wait and
            # then re-sort the refreshed last_used_at values.
            time.sleep(0.1)
            account.last_used_at = utc_now()
            session.commit()
            return selected

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(assign) for _ in range(2)]
        assert {future.result(timeout=10) for future in futures} == {
            "account_0",
            "account_1",
        }


def test_same_worker_cannot_reclaim_an_active_task_lease(ecommerce_postgres):
    kwargs = dict(
        profile_id="profile-0",
        platform="jd",
        task_id="task_parallel",
        owner_id="worker_parallel",
    )
    assert _acquire_profile_lease(ecommerce_postgres, **kwargs, ttl_seconds=0) == 1
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return _acquire_profile_lease(ecommerce_postgres, **kwargs, allow_reentry=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results.count(None) == 1
    assert results.count(2) == 1
