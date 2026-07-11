import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import TrendingDashboard from './pages/TrendingDashboard'
import Historical from './pages/Historical'
import Tickers from './pages/Tickers'
import TickerDetail from './pages/TickerDetail'
import Sources from './pages/Sources'
import RedditSource from './pages/RedditSource'
import SubredditDetail from './pages/SubredditDetail'
import TickerTags from './pages/TickerTags'
import ProcessMonitor from './pages/ProcessMonitor'
import SystemStatus from './pages/SystemStatus'
import Entities from './pages/Entities'
import EntityDetail from './pages/EntityDetail'
import AuthorDetail from './pages/AuthorDetail'
import Trading from './pages/Trading'
import StrategyDetail from './pages/StrategyDetail'
import TradeHistory from './pages/TradeHistory'
import Bots from './pages/Bots'
import BotDetail from './pages/BotDetail'

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<TrendingDashboard />} />
        <Route path="/historical" element={<Historical />} />
        <Route path="/tickers" element={<Tickers />} />
        <Route path="/tickers/:ticker" element={<TickerDetail />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="/sources/reddit/:subreddit" element={<SubredditDetail />} />
        <Route path="/sources/reddit" element={<RedditSource />} />
        <Route path="/entities" element={<Entities />} />
        <Route path="/entities/:entityText" element={<EntityDetail />} />
        <Route path="/authors/:username" element={<AuthorDetail />} />
        <Route path="/trading" element={<Trading />} />
        <Route path="/trading/strategies/:id" element={<StrategyDetail />} />
        <Route path="/trading/history" element={<TradeHistory />} />
        <Route path="/trading/bots" element={<Bots />} />
        <Route path="/trading/bots/:botId" element={<BotDetail />} />
        <Route path="/ticker-tags" element={<TickerTags />} />
        <Route path="/processes" element={<ProcessMonitor />} />
        <Route path="/system" element={<SystemStatus />} />
      </Route>
    </Routes>
  )
}

export default App
