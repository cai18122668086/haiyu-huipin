"""Read compact history once per document version, without repeated TOAST reads."""

from collections import OrderedDict
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from .models import ReplicaDocumentRecord as R
from .models import utc_now

_cache: OrderedDict[tuple[str, str, str, datetime, str], dict[str, Any]] = OrderedDict()
_lock = Lock()
_limit = 2000


def creation_rows(session: Session, workspace_id: str) -> list[dict[str, Any]]:
    # Headers are small even when a private checkpoint occupies many MB. They
    # enforce scope/retention on every read and invalidate each cached version.
    headers = (
        session.execute(
            select(R.id, R.status, R.created_at, R.updated_at)
            .where(
                R.workspace_id == workspace_id,
                R.kind == "session",
                R.updated_at >= utc_now() - timedelta(days=30),
            )
            .order_by(R.created_at.desc(), R.id.desc())
            .limit(100)
        )
        .mappings()
        .all()
    )
    bind = session.get_bind()
    namespace = str(bind.engine.url if isinstance(bind, Connection) else bind.url)

    def key(row: Any) -> tuple[str, str, str, datetime, str]:
        return namespace, workspace_id, row["id"], row["updated_at"], row["status"]

    found: dict[str, dict[str, Any]] = {}
    with _lock:
        for row in headers:
            cached = _cache.get(key(row))
            if cached is not None:
                found[row["id"]] = cached
                _cache.move_to_end(key(row))
    missing = [r["id"] for r in headers if r["id"] not in found]
    if missing:
        if bind.dialect.name == "postgresql":
            # One record expansion detoasts a checkpoint once, rather than once
            # for every extracted field. Never transfer the private JSON blob.
            query = text("""
                SELECT d.id,d.status,d.created_at,d.updated_at,
                  v.replication_method AS method,v.job_ids,v.retry_events,v.progress,
                  v.analysis_stage AS stage,v.analysis_progress AS analysis,
                  v.execution_progress AS execution,v."references"
                FROM replica_documents d
                CROSS JOIN LATERAL jsonb_to_record(CAST(d.payload AS jsonb)) AS v (
                  replication_method text,job_ids jsonb,retry_events jsonb,
                  progress integer,analysis_stage text,analysis_progress jsonb,
                  execution_progress jsonb,"references" jsonb)
                WHERE d.workspace_id=:workspace AND d.kind='session' AND d.id IN :ids
            """).bindparams(bindparam("ids", expanding=True))
            rows = (
                session.execute(query, {"workspace": workspace_id, "ids": missing})
                .mappings()
                .all()
            )
        else:
            rows = (
                session.execute(
                    select(
                        R.id,
                        R.status,
                        R.created_at,
                        R.updated_at,
                        R.payload["replication_method"].label("method"),
                        R.payload["job_ids"].label("job_ids"),
                        R.payload["retry_events"].label("retry_events"),
                        R.payload["progress"].label("progress"),
                        R.payload["analysis_stage"].label("stage"),
                        R.payload["analysis_progress"].label("analysis"),
                        R.payload["execution_progress"].label("execution"),
                        R.payload["references"].label("references"),
                    ).where(
                        R.workspace_id == workspace_id,
                        R.kind == "session",
                        R.id.in_(missing),
                    )
                )
                .mappings()
                .all()
            )
        with _lock:
            for row in rows:
                item = dict(row)
                found[row["id"]] = item
                _cache[key(row)] = item
                _cache.move_to_end(key(row))
            while len(_cache) > _limit:
                _cache.popitem(last=False)
    return [found[r["id"]] for r in headers if r["id"] in found]
