import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import NewInvestigation from './pages/NewInvestigation'
import Workspace from './pages/Workspace'
import Tasks from './pages/Tasks'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="new" element={<NewInvestigation />} />
        <Route path="investigations/:id" element={<Workspace />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
