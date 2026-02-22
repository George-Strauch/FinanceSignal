import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import TrendingDashboard from './pages/TrendingDashboard'
import Tickers from './pages/Tickers'
import TickerDetail from './pages/TickerDetail'
import SystemStatus from './pages/SystemStatus'

function Subreddits() {
  return <h1>Subreddits</h1>
}

function ScraperMonitor() {
  return <h1>Scraper Monitor</h1>
}

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<TrendingDashboard />} />
        <Route path="/tickers" element={<Tickers />} />
        <Route path="/tickers/:ticker" element={<TickerDetail />} />
        <Route path="/subreddits" element={<Subreddits />} />
        <Route path="/scraper" element={<ScraperMonitor />} />
        <Route path="/system" element={<SystemStatus />} />
      </Route>
    </Routes>
  )
}

export default App
