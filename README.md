# CRISIS-X

Dynamic Disaster Digital Twin and Response Intelligence Platform — a
geospatial decision-support platform combining terrain intelligence,
Earth-observation imagery, weather/rainfall, infrastructure,
population/exposure data, hazard modeling, risk analysis, and emergency
response planning.

CRISIS-X is developed independently of TERRAIN-X (a separate
terrain-reconstruction project built by another team) but can optionally
import TERRAIN-X's standardized outputs.

## Status: Phase 0 — Foundations

This is the initial scaffold only: a FastAPI backend with a health check, a
React + TypeScript + Vite frontend, and local Postgres/PostGIS + Redis via
Docker Compose. No domain functionality (Data Hub, hazard modeling, etc.)
exists yet — see `docs/architecture/` for the phased build plan.

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker Desktop (for the Postgres/PostGIS and Redis containers)
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

### 2. Start datastores

```powershell
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d
```

### 3. Backend

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Visit http://localhost:8000/health and http://localhost:8000/docs.

### 4. Frontend

```powershell
cd apps/web
npm install
npm run dev
```

Visit http://localhost:5173 — it should show an "API: online" badge once the
backend is running.

## Repository layout

- `apps/api` — FastAPI backend
- `apps/web` — React + TypeScript + Vite frontend
- `infra/compose` — local Postgres/PostGIS + Redis via Docker Compose
- `docs/architecture` — architecture decision records and the phased build plan
- `scripts` — dev bootstrap helpers

Domain packages (`packages/geo-core`, `packages/hazard-engine`, etc.) and the
async worker service are introduced in later phases; see
`docs/architecture/0001-phase-0-foundations.md`.
