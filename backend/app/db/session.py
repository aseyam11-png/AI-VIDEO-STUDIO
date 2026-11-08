from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_lock = RLock()
_engine_url: str | None = None


def get_database_url() -> str:
    return settings.database_url or settings.local_database_url()


def _build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if not url.endswith(":memory:") and not url.endswith("://"):
            database_path = url.rsplit("/", 1)[-1]
            if database_path and database_path != ":memory:":
                Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url,
            echo=settings.database_echo,
            pool_pre_ping=True,
            connect_args=connect_args,
        )

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    return create_engine(
        url,
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


def get_engine() -> Engine:
    global _engine, _session_factory, _engine_url
    url = get_database_url()
    with _engine_lock:
        if _engine is None or _engine_url != url:
            if _engine is not None:
                _engine.dispose()
            _engine = _build_engine(url)
            _session_factory = sessionmaker(
                bind=_engine,
                autoflush=False,
                expire_on_commit=False,
                class_=Session,
            )
            _engine_url = url
        return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def configure_database(url: str) -> None:
    """Replace the engine; intended for isolated tests and controlled tooling."""
    global _engine, _session_factory, _engine_url
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = _build_engine(url)
        _session_factory = sessionmaker(
            bind=_engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )
        _engine_url = url


def dispose_database() -> None:
    global _engine, _session_factory, _engine_url
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
        _engine_url = None
