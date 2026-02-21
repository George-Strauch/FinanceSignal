import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'

function Tickers() {
  return <h1>Tickers</h1>
}

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
        <Route path="/" element={<Dashboard />} />
        <Route path="/tickers" element={<Tickers />} />
        <Route path="/subreddits" element={<Subreddits />} />
        <Route path="/scraper" element={<ScraperMonitor />} />
      </Route>
    </Routes>
  )
}

export default App
