from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.db.sqlite_pragma import enable_sqlite_foreign_keys
from app.main import app
from app.services import storage as storage_module


@pytest.fixture()
def tmp_storage_root(tmp_path: Path) -> Generator[Path, None, None]:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    original = storage_module.settings.data_storage_root
    storage_module.settings.data_storage_root = str(storage_root)
    yield storage_root
    storage_module.settings.data_storage_root = original


@pytest.fixture()
def client(tmp_path: Path, tmp_storage_root: Path) -> Generator[TestClient, None, None]:
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    enable_sqlite_foreign_keys(engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture()
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
