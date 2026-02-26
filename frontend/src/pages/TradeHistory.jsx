import { useEffect, useState, useCallback } from 'react'
import { get } from '../api/client'
import PositionsTable from '../components/PositionsTable'
import './TradeHistory.css'

export default function TradeHistory() {
  const [trades, setTrades] = useState([])
  const [strategies, setStrategies] = useState([])
  const [loading, setLoading] = useState(true)
  const [filterStrategy, setFilterStrategy] = useState('')
  const [filterTicker, setFilterTicker] = useState('')
  const [filterStatus, setFilterStatus] = useState('')

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await get('/trading/strategies')
      setStrategies(res.strategies || [])
    } catch {}
  }, [])

  const fetchTrades = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filterStrategy) params.set('strategy_id', filterStrategy)
      if (filterTicker) params.set('ticker', filterTicker.toUpperCase())
      if (filterStatus) params.set('status', filterStatus)
      params.set('limit', '500')
      const res = await get(`/trading/trades?${params}`)

      // Enrich with strategy titles
      const stratMap = {}
      for (const s of strategies) stratMap[s.id] = s.title
      for (const t of res.trades || []) t.strategy_title = stratMap[t.strategy_id] || `#${t.strategy_id}`

      setTrades(res.trades || [])
    } catch {
      setTrades([])
    } finally {
      setLoading(false)
    }
  }, [filterStrategy, filterTicker, filterStatus, strategies])

  useEffect(() => { fetchStrategies() }, [fetchStrategies])
  useEffect(() => { fetchTrades() }, [fetchTrades])

  return (
    <div className="th-page">
      <h1>Trade History</h1>

      <div className="th-filters">
        <select value={filterStrategy} onChange={e => setFilterStrategy(e.target.value)}>
          <option value="">All Strategies</option>
          {strategies.map(s => (
            <option key={s.id} value={s.id}>{s.title}</option>
          ))}
        </select>

        <input
          type="text"
          placeholder="Filter by ticker..."
          value={filterTicker}
          onChange={e => setFilterTicker(e.target.value)}
        />

        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="">All Status</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </select>
      </div>

      <div className="th-section">
        {loading ? (
          <div className="th-loading">Loading trades...</div>
        ) : (
          <PositionsTable
            trades={trades}
            showStrategy={true}
            showClose={filterStatus !== 'closed'}
            onTradeUpdated={fetchTrades}
          />
        )}
      </div>
    </div>
  )
}
