# ADR 0002: Phase 1 SQLite Fallback (Docker Unavailable)

Date: 2026-09-16
Status: Accepted

## Context

Phase 1 (Data Hub) was planned against Postgres + PostGIS running via Docker
Desktop (`infra/compose/docker-compose.yml`, established in ADR 0001).
Docker Desktop is not starting on the development machine, and WSL/Ubuntu is
explicitly out of scope. The project must not block on this.

Redis was never required for Phase 1: async job processing was always scoped
to Phase 4, and Phase 1 uploads (hackathon-scale files) validate synchronously
inside the API request. Docker being unavailable therefore only actually
blocks the database, not the job queue.

## Decision

- Phase 0's committed configuration is untouched: `.env.example` (repo root
  and `apps/api/`) and `infra/compose/docker-compose.yml` still default to
  Postgres/PostGIS + Redis. That remains the target production architecture.
- Locally, only the uncommitted `apps/api/.env` is overridden to
  `DATABASE_URL=sqlite:///./data/dev/crisisx.db`. The same SQLAlchemy models
  and the same Alembic migration run unmodified against SQLite.
- `alembic/env.py` now targets `Base.metadata` (previously `None`, since no
  models existed in Phase 0) and enables `render_as_batch` when the
  connected dialect is SQLite, since SQLite can't `ALTER` columns in place —
  required for any future migration that alters an existing column.
- **No PostGIS-specific types are introduced.** `Dataset.footprint_geojson`
  is a plain `Text` column holding GeoJSON, and bounding boxes are four
  plain `Float` columns (`bbox_min_x/min_y/max_x/max_y`), not a PostGIS
  `Geometry` column. This schema is byte-for-byte identical whether the
  target database is SQLite or Postgres — there is nothing to migrate when
  switching backends, only the connection string changes.
- Uploaded files are stored on the local filesystem
  (`apps/api/data/storage/{project_id}/{dataset_id}/{filename}`, configurable
  via `DATA_STORAGE_ROOT`), never as DB blobs, so this decision is unaffected
  by which database engine is active.
- `apps/api/data/` (both the SQLite file and stored uploads) is gitignored.

## Consequences

- Full Data Hub functionality (project/dataset CRUD, upload, CRS/bbox
  extraction, validation) works today with zero external services running.
- **Revert path, once Postgres/PostGIS is reachable again** (Docker fixed,
  or a native Windows Postgres install): change `DATABASE_URL` back to the
  Postgres DSN in `apps/api/.env`, run `alembic upgrade head` against it
  (the existing migration applies as-is — it only uses portable column
  types), and re-upload or migrate any local SQLite data if it needs to be
  preserved. No application code changes are required.
- **Deferred, not solved:** real spatial SQL (`ST_Intersects`, spatial
  indexes) is unavailable until Postgres/PostGIS is back. This does not
  block Phase 1 (Data Hub needs no spatial queries), but it will block parts
  of Phase 2 (Geospatial Processing) and, more directly, Phase 7 (Exposure
  Engine), which needs spatial intersection of hazard extent with
  infrastructure. Those phases should either wait for Postgres/PostGIS or
  explicitly re-scope to use `shapely`/`geopandas` for in-process spatial
  operations instead of database-side spatial SQL — a decision to make
  explicitly when that phase starts, not assumed now.
- Tests never depend on Postgres or Docker: `tests/conftest.py` builds an
  isolated temp-file SQLite database per test run via a `get_db` dependency
  override, matching the pattern already used for Phase 0's tests.
