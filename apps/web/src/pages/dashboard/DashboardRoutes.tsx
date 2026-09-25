import { Route, Routes } from 'react-router-dom'
import { DashboardPage } from './DashboardPage'

export function DashboardRoutes() {
  return (
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/projects/:projectId" element={<DashboardPage />} />
    </Routes>
  )
}
