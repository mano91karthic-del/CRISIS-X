# CRISIS-X

Dynamic Disaster Digital Twin and Response Intelligence Platform — a
geospatial decision-support platform combining terrain intelligence,
Earth-observation imagery, weather/rainfall, infrastructure,
population/exposure data, hazard modeling, risk analysis, and emergency
response planning.

CRISIS-X is developed independently of TERRAIN-X (a separate
terrain-reconstruction project built by another team) but can optionally
import TERRAIN-X's standardized outputs.

## Status: Phase 12 — AI Assistant

Phases 0-11 (Foundations through the 2D/3D Command Dashboard) are
implemented, plus Phase 12: a read-only AI Assistant that answers
natural-language questions about a project's Digital Twin, hazard/
exposure/risk/route analyses, and scenarios, grounded entirely in
already-computed CRISIS-X data (it never invents a result, and says so
explicitly when information isn't available). See `docs/architecture/`
for the full phased build plan and ADRs, and
`docs/architecture/0013-phase-12-ai-assistant.md` for the assistant's
architecture specifically.

The assistant works out of the box with **no AI provider configured**:
it runs against a deterministic, fully offline `FakeProvider` by
default. `AI_PROVIDER_API_KEY`/`AI_PROVIDER_MODEL` in
`apps/api/.env.example` are optional and commented out -- a real AI
vendor SDK is intentionally not wired in yet (see the ADR).

**Docker Desktop is currently not required.** It's unavailable on the dev
machine, so the database defaults to local SQLite for now — see
`docs/architecture/0002-phase-1-sqlite-fallback.md`. Postgres/PostGIS via
Docker Compose remains the committed target architecture and the default in
every committed `.env.example`; only your local, uncommitted
`apps/api/.env` overrides it.

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker Desktop — optional for now (see above); needed once Postgres/PostGIS
  or Redis are actually required
- Native Windows development — no WSL required or used

## Getting started

Run `scripts/bootstrap.ps1` to do all of the below automatically, or do it
by hand:

### 1. Environment files

```powershell
Copy-Item .env.example .env
Copy-Item apps/api/.env.example apps/api/.env
Copy-Item apps/web/.env.example apps/web/.env
```

For local dev without Docker, edit `apps/api/.env` and set:

```
DATABASE_URL=sqlite:///./data/dev/crisisx.db
```

### 2. (Optional) Start datastores

Only needed once you switch `DATABASE_URL` back to Postgres, or once Redis
is required (Phase 4+):

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d
```

### 3. Backend

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\alembic upgrade head
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Visit http://localhost:8000/health and http://localhost:8000/docs.

### 4. Frontend

```powershell
cd apps/web
npm install
npm run dev
```

Visit http://localhost:5173 — it should show an "API: online" badge and the
Data Hub UI (create a project, upload a dataset, see extracted CRS/bbox and
validation status).

## Repository layout

- `apps/api` — FastAPI backend
- `apps/web` — React + TypeScript + Vite frontend
- `infra/compose` — local Postgres/PostGIS + Redis via Docker Compose
- `docs/architecture` — architecture decision records and the phased build plan
- `scripts` — dev bootstrap helpers

Domain packages (`packages/geo-core`, `packages/hazard-engine`, etc.) and the
async worker service are introduced in later phases; see
`docs/architecture/0001-phase-0-foundations.md`.
