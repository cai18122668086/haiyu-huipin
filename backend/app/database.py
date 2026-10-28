from collections.abc import Callable, Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection


class Base(DeclarativeBase):
    pass


def build_database(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: float = 5,
    pool_recycle: int = 1800,
) -> tuple[Engine, sessionmaker[Session]]:
    connect_args: dict[str, object]
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        pool_options: dict[str, object] = {}
    else:
        # Fail promptly when the database is unavailable or the existing
        # bounded pool is occupied; increasing every process's pool would
        # move the overload to PostgreSQL instead of fixing connection use.
        connect_args = {"connect_timeout": 5}
        pool_options = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
        }
    engine = create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=True,
        **pool_options,
    )
    session_factory = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
    )
    return engine, session_factory


def get_session(request: HTTPConnection) -> Iterator[Session]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session


def read_snapshot[**P, R](
    session: Session,
    operation: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Finish read-only ORM work and detach its results before external I/O.

    Call this in a worker thread. Returned records retain loaded scalar fields,
    while rollback returns their connection to the bounded pool.
    """
    try:
        return operation(*args, **kwargs)
    finally:
        session.expunge_all()
        session.rollback()


async def read_snapshot_async[**P, R](
    session: Session,
    operation: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    def load() -> R:
        return read_snapshot(session, operation, *args, **kwargs)

    return await run_in_threadpool(load)
