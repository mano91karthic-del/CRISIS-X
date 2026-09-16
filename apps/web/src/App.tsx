import { useEffect, useState } from 'react'
import { getHealth } from './lib/api'

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
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-950 text-slate-100">
      <h1 className="text-3xl font-semibold tracking-tight">CRISIS-X</h1>
      <p className="text-slate-400">Disaster Digital Twin &amp; Response Intelligence Platform</p>
      <div className="flex items-center gap-2 rounded-full border border-slate-800 px-4 py-2 text-sm">
        <span className={`h-2 w-2 rounded-full ${dotClass}`} />
        API: {status}
      </div>
    </div>
  )
}

export default App
