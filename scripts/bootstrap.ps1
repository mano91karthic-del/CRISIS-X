# Bootstraps local dev environment for CRISIS-X (native Windows, no WSL).
# Creates the Python venv, installs backend/frontend deps, and copies .env
# example files where a real .env doesn't already exist.

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path "$PSScriptRoot/.."

Write-Host "== CRISIS-X Phase 0 bootstrap ==" -ForegroundColor Cyan

# --- Backend ---
Push-Location "$RepoRoot/apps/api"
if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python venv..." -ForegroundColor Yellow
    python -m venv .venv
}
& ".venv/Scripts/pip.exe" install --upgrade pip
& ".venv/Scripts/pip.exe" install -r requirements.txt
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}
Pop-Location

# --- Frontend ---
Push-Location "$RepoRoot/apps/web"
Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
npm install
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}
Pop-Location

# --- Root env for docker-compose ---
if (-not (Test-Path "$RepoRoot/.env")) {
    Copy-Item "$RepoRoot/.env.example" "$RepoRoot/.env"
}

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Green
Write-Host "  1. docker compose --env-file .env -f infra/compose/docker-compose.yml up -d"
Write-Host "  2. cd apps/api; .venv\Scripts\uvicorn app.main:app --reload --port 8000"
Write-Host "  3. cd apps/web; npm run dev"
