import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Phase 11: exactly one scoped E2E spec asserting the dashboard actually
// renders real WebGL content -- see ADR 0012 §26. A dedicated SQLite DB
// + storage dir (created by apps/api/scripts/init_e2e_db.py, the same
// Base.metadata.create_all() mechanism tests/conftest.py already uses)
// keeps this from touching the real local dev database.
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const apiDir = path.resolve(__dirname, '../api')
const e2eDbPath = path.resolve(apiDir, 'data/e2e/crisisx.db')
const e2eStorageRoot = path.resolve(apiDir, 'data/e2e/storage')

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `"${path.join(apiDir, '.venv/Scripts/python.exe')}" scripts/init_e2e_db.py && "${path.join(
        apiDir,
        '.venv/Scripts/python.exe',
      )}" -m uvicorn app.main:app --port 8000`,
      cwd: apiDir,
      url: 'http://localhost:8000/health',
      reuseExistingServer: false,
      env: {
        DATABASE_URL: `sqlite:///${e2eDbPath}`,
        DATA_STORAGE_ROOT: e2eStorageRoot,
        CORS_ORIGINS: 'http://localhost:5173',
      },
      timeout: 30_000,
    },
    {
      command: 'npm run dev',
      cwd: __dirname,
      url: 'http://localhost:5173',
      reuseExistingServer: false,
      env: { VITE_EXPOSE_MAP_FOR_TESTS: 'true' },
      timeout: 30_000,
    },
  ],
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
