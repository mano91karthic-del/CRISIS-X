# AGENTS.md

## Project

CRISIS-X — geospatial disaster digital twin and response intelligence platform.
FastAPI backend + React/Vite frontend. Native Windows development (no WSL).

## Repository layout

- `apps/api` — FastAPI backend (Python 3.11+)
- `apps/web` — React + TypeScript + Vite frontend (Node.js 20+)
- `infra/compose` — Docker Compose for Postgres/PostGIS + Redis
- `docs/architecture` — ADRs and phased build plan (Phase 0–12)
- `scripts` — dev bootstrap helpers

## Setup

Run `scripts/bootstrap.ps1` to create venvs, install deps, and copy `.env` files.
Or do it manually:

```powershell
# Backend
cd apps/api
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\alembic upgrade head

# Frontend
cd apps/web
npm install
```

## Running locally

```powershell
# Backend (from apps/api)
.venv\Scripts\uvicorn app.main:app --reload --port 8000

# Frontend (from apps/web)
npm run dev
```

API docs at http://localhost:8000/docs. Frontend at http://localhost:5173.

## Database

Default `DATABASE_URL` in `.env.example` points to Postgres via Docker Compose.
For local dev without Docker, set in `apps/api/.env`:

```
DATABASE_URL=sqlite:///./data/dev/crisisx.db
```

Run migrations: `.venv\Scripts\alembic upgrade head` (from `apps/api`).

SQLite quirks handled automatically:
- `PRAGMA foreign_keys=ON` is enabled per-connection (`app/db/sqlite_pragma.py`)
- Alembic uses `render_as_batch` for SQLite (can't ALTER columns directly)

## Testing

```powershell
# Backend (from apps/api)
.venv\Scripts\pytest

# Frontend (from apps/web)
npm test              # vitest
npm run typecheck     # tsc -b
npm run e2e           # playwright
```

Backend tests use SQLite (not Postgres) via `TestClient` and dependency overrides
in `tests/conftest.py`. No Docker or external services needed.

## API architecture

- `app/api/` — FastAPI routers (thin, delegate to services)
- `app/services/` — business logic
- `app/models/` — SQLAlchemy ORM models
- `app/schemas/` — Pydantic request/response schemas
- `app/core/config.py` — pydantic-settings, reads `.env`
- `app/db/` — engine, session, SQLite pragma

## Web architecture

- `src/pages/` — route-level components (DataHubPage, dashboard/)
- `src/components/` — reusable UI
- `src/lib/api/` — API client
- `src/state/` — zustand stores
- `src/geo/` — geo/map utilities

## AI Assistant (Phase 12)

Read-only assistant that answers from already-computed CRISIS-X data.
Runs fully offline against a deterministic `FakeProvider` by default —
no AI provider key needed. `AI_PROVIDER_API_KEY`/`AI_PROVIDER_MODEL` in
`apps/api/.env` are optional and unused until a real provider is wired in.

Key files:
- `app/services/assistant/orchestrator.py` — keyword-based tool selection
- `app/services/assistant/provider.py` — FakeProvider + AIProvider protocol
- `app/services/assistant/tools.py` — read-only tool wrappers around existing endpoints

## Docker (optional)

Only needed when switching back to Postgres or using Redis:

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d
```

Docker Desktop is not required for local dev with SQLite.

## Environment files

- `.env` (root) — used by docker-compose only
- `apps/api/.env` — API config (DATABASE_URL, CORS_ORIGINS, etc.)
- `apps/web/.env` — VITE_API_BASE_URL (defaults to http://localhost:8000)

All have `.env.example` counterparts. Copy and edit as needed.
