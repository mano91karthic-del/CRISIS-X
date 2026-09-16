# ADR 0001: Phase 0 Foundations

Date: 2026-09-16
Status: Accepted

## Context

CRISIS-X starts from an empty repository. Before any domain functionality
(Data Hub, Terrain Adapter, Hazard Engine, etc.) is built, we need a minimal,
runnable skeleton that proves the chosen stack works end-to-end on the native
Windows development environment.

## Decision

Phase 0 establishes, and nothing more:

- FastAPI backend (`apps/api`) with settings via `pydantic-settings`,
  structured logging, a `/health` endpoint, and an Alembic setup with no
  schema yet.
- React + TypeScript + Vite + Tailwind frontend (`apps/web`) with a single
  page that calls `/health` and shows API connectivity status.
- Postgres 16 + PostGIS and Redis 7 as Docker Desktop containers
  (`infra/compose`), with application code running natively on Windows —
  no WSL involved anywhere in the toolchain.
- Configuration via `.env` files (`.env.example` files are committed,
  real `.env` files are gitignored).

Deliberately excluded from Phase 0: domain packages (geo-core, hazard-engine,
etc.), the RQ worker service, database models/schema, and frontend mapping
libraries (MapLibre GL JS, Three.js). These arrive with their respective
later phases.

## Consequences

- `docker compose up`, `uvicorn app.main:app`, and `npm run dev` are each
  independently runnable and form the baseline every later phase builds on.
- Alembic is wired to real settings now, so later phases only need to add
  SQLAlchemy models and run `alembic revision --autogenerate` — no
  re-plumbing of configuration.
- No GDAL/geospatial or AI dependencies are installed yet; those arrive with
  Phase 2 (Terrain Adapter) and Phase 12 (EO AI Engine) respectively, keeping
  the Phase 0 install surface small.
