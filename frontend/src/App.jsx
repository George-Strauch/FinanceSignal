import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import TrendingDashboard from './pages/TrendingDashboard'
import Tickers from './pages/Tickers'
import TickerDetail from './pages/TickerDetail'
import Sources from './pages/Sources'
import RedditSource from './pages/RedditSource'
import ProcessMonitor from './pages/ProcessMonitor'
import SystemStatus from './pages/SystemStatus'

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<TrendingDashboard />} />
        <Route path="/tickers" element={<Tickers />} />
        <Route path="/tickers/:ticker" element={<TickerDetail />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="/sources/reddit" element={<RedditSource />} />
        <Route path="/processes" element={<ProcessMonitor />} />
        <Route path="/system" element={<SystemStatus />} />
      </Route>
    </Routes>
  )
}

export default App
