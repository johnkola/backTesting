import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import ServiceGate from './components/ServiceGate'
import HomePage from './pages/HomePage'
import SourcesPage from './pages/SourcesPage'
import InstrumentsPage from './pages/InstrumentsPage'
import ImportsPage from './pages/ImportsPage'
import ResultsPage from './pages/ResultsPage'
import RunPage from './pages/RunPage'
import ResultDetailPage from './pages/ResultDetailPage'
import ModelsPage from './pages/ModelsPage'
import DocsPage from './pages/DocsPage'
import DocHistoryPage from './pages/DocHistoryPage'

export default function App() {
  return (
    // Nothing renders until every service has answered — see ServiceGate.
    <ServiceGate>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/sources" element={<SourcesPage />} />
            <Route path="/instruments" element={<InstrumentsPage />} />
            <Route path="/imports" element={<ImportsPage />} />
            <Route path="/run" element={<RunPage />} />
            <Route path="/results" element={<ResultsPage />} />
            <Route path="/results/:id" element={<ResultDetailPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/docs" element={<Navigate to="/docs/getting-started" replace />} />
            <Route path="/docs/:slug" element={<DocsPage />} />
            <Route path="/docs/:slug/history" element={<DocHistoryPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ServiceGate>
  )
}
