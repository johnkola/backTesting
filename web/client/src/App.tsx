import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import PipelinePage from './pages/PipelinePage'
import BacktestStage from './pages/pipeline/BacktestStage'
import DataStage from './pages/pipeline/DataStage'
import FeaturesStage from './pages/pipeline/FeaturesStage'
import NormalizationStage from './pages/pipeline/NormalizationStage'
import SplitStage from './pages/pipeline/SplitStage'
import TrainingStage from './pages/pipeline/TrainingStage'
import Layout from './components/Layout'
import ServiceGate from './components/ServiceGate'
import HomePage from './pages/HomePage'
import SourcesPage from './pages/SourcesPage'
import InstrumentsPage from './pages/InstrumentsPage'
import ImportsPage from './pages/ImportsPage'
import ResultsPage from './pages/ResultsPage'
import RunPage from './pages/RunPage'
import TrainPage from './pages/TrainPage'
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
            {/* One tab per pipeline stage, in the order they run. Explicit
                children rather than a :stage param, so an unknown slug is a
                routing concern here instead of a lookup inside the page. */}
            <Route path="/pipeline" element={<PipelinePage />}>
              <Route index element={<Navigate to="/pipeline/data" replace />} />
              <Route path="data" element={<DataStage />} />
              <Route path="features" element={<FeaturesStage />} />
              <Route path="normalization" element={<NormalizationStage />} />
              <Route path="split" element={<SplitStage />} />
              <Route path="training" element={<TrainingStage />} />
              <Route path="backtest" element={<BacktestStage />} />
              <Route path="*" element={<Navigate to="/pipeline/data" replace />} />
            </Route>
            <Route path="/instruments" element={<InstrumentsPage />} />
            <Route path="/imports" element={<ImportsPage />} />
            <Route path="/run" element={<RunPage />} />
            <Route path="/train" element={<TrainPage />} />
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
