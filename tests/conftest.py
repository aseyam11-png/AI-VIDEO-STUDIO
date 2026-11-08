import pytest

from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import configure_database, dispose_database, get_engine


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    database_path = tmp_path / "test-control-plane.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "database_url", database_url)
    configure_database(database_url)
    Base.metadata.create_all(bind=get_engine())
    yield
    Base.metadata.drop_all(bind=get_engine())
    dispose_database()
