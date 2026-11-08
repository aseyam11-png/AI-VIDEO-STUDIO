import pytest

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.session import configure_database, dispose_database, get_engine


@pytest.fixture(autouse=True)
def isolated_database(tmp_path):
    database_path = tmp_path / "test-control-plane.sqlite3"
    configure_database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(bind=get_engine())
    yield
    Base.metadata.drop_all(bind=get_engine())
    dispose_database()
