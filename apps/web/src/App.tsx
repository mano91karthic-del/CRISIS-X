import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'
import { getHealth } from './lib/api'
import { DataHubPage } from './pages/DataHubPage'
import { DashboardRoutes } from './pages/dashboard/DashboardRoutes'

type ApiStatus = 'checking' | 'online' | 'offline'

function App() {
  const [status, setStatus] = useState<ApiStatus>('checking')

  useEffect(() => {
    getHealth()
      .then(() => setStatus('online'))
      .catch(() => setStatus('offline'))
  }, [])

  const dotClass =
    status === 'online' ? 'bg-emerald-500' : status === 'offline' ? 'bg-red-500' : 'bg-amber-500'

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <header className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
        <div className="flex items-center gap-6">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">CRISIS-X</h1>
            <p className="text-xs text-slate-500">Disaster Digital Twin &amp; Response Intelligence Platform</p>
          </div>
          <nav className="flex items-center gap-4 text-sm text-slate-400">
            <Link to="/dashboard" className="hover:text-slate-100">
              Command Dashboard
            </Link>
            <Link to="/data-hub" className="hover:text-slate-100">
              Data Hub (upload/manage)
            </Link>
          </nav>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-slate-800 px-4 py-2 text-sm">
          <span className={`h-2 w-2 rounded-full ${dotClass}`} />
          API: {status}
        </div>
      </header>

      <div className="flex-1">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard/*" element={<DashboardRoutes />} />
          <Route path="/data-hub" element={<DataHubPage />} />
        </Routes>
      </div>
    </div>
  )
}

export default App
