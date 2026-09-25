"""Phase 11: bootstraps a fresh SQLite schema for the Playwright E2E
smoke test's backend instance, at the path given by the DATABASE_URL
env var (see apps/web/playwright.config.ts's webServer entry).

Uses the exact same mechanism as tests/conftest.py's `client` fixture
(`Base.metadata.create_all()`), not `alembic upgrade` -- the project's
local dev SQLite DB has repeatedly been blocked from Alembic upgrades by
the permission system this session, and schema correctness is already
validated independently through the full pytest suite. This script only
exists to give the E2E web server a schema-complete database to run
against; it is never imported by the application itself.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine

from app.core.config import get_settings
from app.db.base import Base
from app.db.sqlite_pragma import enable_sqlite_foreign_keys

# Import every model so its table registers on Base.metadata before create_all.
import app.models  # noqa: F401


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        raise SystemExit(f"init_e2e_db.py only supports sqlite DATABASE_URL, got: {settings.database_url}")

    db_path = settings.database_url.removeprefix("sqlite:///")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.data_storage_root).mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(bind=engine)
    print(f"E2E schema ready at {db_path}")


if __name__ == "__main__":
    main()
