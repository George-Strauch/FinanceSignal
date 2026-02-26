import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { get, post } from '../api/client'
import './Bots.css'

function formatPct(v) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`
  return `${Math.round(diff / 86400)}d ago`
}

export default function Bots() {
  const navigate = useNavigate()
  const [bots, setBots] = useState([])
  const [loading, setLoading] = useState(true)

  const fetchBots = useCallback(async () => {
    setLoading(true)
    try {
      const res = await get('/bots')
      setBots(res.bots || [])
    } catch {
      setBots([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchBots() }, [fetchBots])

  const handleToggleLive = async (e, botId) => {
    e.stopPropagation()
    try {
      await post(`/bots/${botId}/toggle-live`)
      fetchBots()
    } catch {}
  }

  if (loading && bots.length === 0) {
    return <div className="bots-loading">Loading bots...</div>
  }

  return (
    <div className="bots-page">
      <div className="bots-header">
        <h1>Trading Bots</h1>
        <p className="bots-subtitle">
          Automated strategies that evaluate tickers hourly and execute trades.
        </p>
      </div>

      {bots.length === 0 && !loading && (
        <div className="bots-empty">
          <p>No bots found. Add a bot by creating a folder in <code>bots/</code> with a <code>bot.py</code> file.</p>
        </div>
      )}

      <div className="bots-grid">
        {bots.map(bot => {
          const stats = bot.stats || {}
          return (
            <div
              key={bot.bot_id}
              className="bot-card"
              onClick={() => navigate(`/trading/bots/${bot.bot_id}`)}
              style={{ borderTopColor: bot.color }}
            >
              <div className="bot-card-header">
                <div className="bot-card-title-row">
                  <span className="bot-card-dot" style={{ background: bot.color }} />
                  <span className="bot-card-name">{bot.name}</span>
                </div>
                <button
                  className={`bot-live-toggle ${bot.live_trading ? 'active' : ''}`}
                  onClick={(e) => handleToggleLive(e, bot.bot_id)}
                  title={bot.live_trading ? 'Disable live trading' : 'Enable live trading'}
                >
                  <span className="bot-live-dot" />
                  {bot.live_trading ? 'LIVE' : 'OFF'}
                </button>
              </div>

              <p className="bot-card-desc">{bot.description}</p>

              <div className="bot-card-metrics">
                <div className="bot-card-metric">
                  <span className={stats.win_rate != null ? (stats.win_rate >= 0.5 ? 'pnl-positive' : 'pnl-negative') : ''}>
                    {stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '-'}
                  </span>
                  <span className="bot-card-metric-label">Win Rate</span>
                </div>
                <div className="bot-card-metric">
                  <span className={stats.avg_return_pct != null ? (stats.avg_return_pct >= 0 ? 'pnl-positive' : 'pnl-negative') : ''}>
                    {formatPct(stats.avg_return_pct)}
                  </span>
                  <span className="bot-card-metric-label">Avg Return</span>
                </div>
                <div className="bot-card-metric">
                  <span>{stats.total_trades ?? 0}</span>
                  <span className="bot-card-metric-label">Trades</span>
                </div>
                <div className="bot-card-metric">
                  <span>{stats.open_trades ?? 0}</span>
                  <span className="bot-card-metric-label">Open</span>
                </div>
              </div>

              <div className="bot-card-footer">
                <span className="bot-card-evaluated">
                  Evaluated: {timeAgo(bot.last_evaluated_at)}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
