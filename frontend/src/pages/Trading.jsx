import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { get, post } from '../api/client'
import TradeEntryModal from '../components/TradeEntryModal'
import PositionsTable from '../components/PositionsTable'
import './Trading.css'

function formatPct(v) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

export default function Trading() {
  const navigate = useNavigate()
  const [portfolio, setPortfolio] = useState(null)
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [stratModalOpen, setStratModalOpen] = useState(false)
  const [newStrat, setNewStrat] = useState({ title: '', description: '', color: '#6366f1' })
  const [stratSubmitting, setStratSubmitting] = useState(false)

  const fetchPortfolio = useCallback(async () => {
    setLoading(true)
    try {
      const res = await get('/trading/portfolio')
      // Enrich open positions with strategy titles
      const stratMap = {}
      for (const s of res.strategies || []) stratMap[s.id] = s.title
      for (const t of res.open_positions || []) t.strategy_title = stratMap[t.strategy_id] || `#${t.strategy_id}`
      setPortfolio(res)
    } catch {
      setPortfolio(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchPortfolio() }, [fetchPortfolio])

  const handleCreateStrategy = async (e) => {
    e.preventDefault()
    if (!newStrat.title.trim()) return
    setStratSubmitting(true)
    try {
      await post('/trading/strategies', newStrat)
      setStratModalOpen(false)
      setNewStrat({ title: '', description: '', color: '#6366f1' })
      fetchPortfolio()
    } catch {}
    setStratSubmitting(false)
  }

  const summary = portfolio?.summary
  const strategies = portfolio?.strategies || []
  const openPositions = portfolio?.open_positions || []

  return (
    <div className="trading-page">
      <div className="trading-header">
        <h1>Paper Trading</h1>
        <div className="trading-header-actions">
          <button className="trading-new-strat-btn" onClick={() => setStratModalOpen(true)}>
            + New Strategy
          </button>
          <button className="trading-new-trade-btn" onClick={() => setModalOpen(true)}>
            + New Trade
          </button>
          <button className="trading-history-btn" onClick={() => navigate('/trading/history')}>
            Trade History
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="trading-summary-cards">
        <div className="trading-summary-card">
          <div className="trading-summary-value">{summary?.total_open ?? '-'}</div>
          <div className="trading-summary-label">Open Positions</div>
        </div>
        <div className="trading-summary-card">
          <div className="trading-summary-value">{summary?.total_closed ?? '-'}</div>
          <div className="trading-summary-label">Closed Trades</div>
        </div>
        <div className="trading-summary-card">
          <div className={`trading-summary-value ${summary?.overall_win_rate != null ? (summary.overall_win_rate >= 0.5 ? 'pnl-positive' : 'pnl-negative') : ''}`}>
            {summary?.overall_win_rate != null ? `${(summary.overall_win_rate * 100).toFixed(1)}%` : '-'}
          </div>
          <div className="trading-summary-label">Win Rate</div>
        </div>
        <div className="trading-summary-card">
          <div className={`trading-summary-value ${summary?.avg_return_pct != null ? (summary.avg_return_pct >= 0 ? 'pnl-positive' : 'pnl-negative') : ''}`}>
            {formatPct(summary?.avg_return_pct)}
          </div>
          <div className="trading-summary-label">Avg Return</div>
        </div>
      </div>

      {/* Strategy Comparison */}
      {strategies.length > 0 && (
        <div className="trading-section">
          <h2>Strategy Comparison</h2>
          <div className="trading-strat-compare">
            {strategies.map(s => {
              const stats = s.stats || {}
              const winRate = stats.win_rate != null ? (stats.win_rate * 100).toFixed(1) : null
              const avgRet = stats.avg_return_pct
              const barWidth = winRate != null ? winRate : 0
              return (
                <div key={s.id} className="trading-strat-compare-row">
                  <div className="trading-strat-compare-name">
                    <span className="trading-strat-swatch" style={{ background: s.color }} />
                    <button className="trading-strat-name-link" onClick={() => navigate(`/trading/strategies/${s.id}`)}>
                      {s.title}
                    </button>
                  </div>
                  <div className="trading-strat-compare-bar-track">
                    <div
                      className="trading-strat-compare-bar-fill"
                      style={{ width: `${barWidth}%`, background: s.color }}
                    />
                  </div>
                  <div className="trading-strat-compare-stats">
                    <span>{winRate != null ? `${winRate}% WR` : '-'}</span>
                    <span className={avgRet != null ? (avgRet >= 0 ? 'pnl-positive' : 'pnl-negative') : ''}>
                      {formatPct(avgRet)}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Open Positions */}
      <div className="trading-section">
        <h2>Open Positions</h2>
        {loading && openPositions.length === 0 ? (
          <div className="trading-loading">Loading...</div>
        ) : (
          <PositionsTable trades={openPositions} onTradeUpdated={fetchPortfolio} />
        )}
      </div>

      {/* Strategies Grid */}
      <div className="trading-section">
        <h2>Strategies</h2>
        {strategies.length === 0 && !loading && (
          <p className="trading-empty">No strategies yet. Create one to start trading.</p>
        )}
        <div className="trading-strat-grid">
          {strategies.map(s => {
            const stats = s.stats || {}
            return (
              <div
                key={s.id}
                className="trading-strat-card"
                onClick={() => navigate(`/trading/strategies/${s.id}`)}
                style={{ borderTopColor: s.color }}
              >
                <div className="trading-strat-card-header">
                  <span className="trading-strat-card-title">
                    {s.bot_id && <span className="trading-bot-badge">BOT</span>}
                    {s.title}
                  </span>
                  <span className="trading-strat-card-badge" style={{ background: s.color }}>
                    {stats.open_trades ?? 0} open
                  </span>
                </div>
                <div className="trading-strat-card-metrics">
                  <div className="trading-strat-metric">
                    <span className={stats.win_rate != null ? (stats.win_rate >= 0.5 ? 'pnl-positive' : 'pnl-negative') : ''}>
                      {stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '-'}
                    </span>
                    <span className="trading-strat-metric-label">Win Rate</span>
                  </div>
                  <div className="trading-strat-metric">
                    <span className={stats.avg_return_pct != null ? (stats.avg_return_pct >= 0 ? 'pnl-positive' : 'pnl-negative') : ''}>
                      {formatPct(stats.avg_return_pct)}
                    </span>
                    <span className="trading-strat-metric-label">Avg Return</span>
                  </div>
                  <div className="trading-strat-metric">
                    <span>{stats.total_trades ?? 0}</span>
                    <span className="trading-strat-metric-label">Closed</span>
                  </div>
                  <div className="trading-strat-metric">
                    <span>{stats.profit_factor != null ? stats.profit_factor.toFixed(2) : '-'}</span>
                    <span className="trading-strat-metric-label">Profit Factor</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Strategy Creation Modal */}
      {stratModalOpen && (
        <div className="trade-modal-backdrop" onClick={() => setStratModalOpen(false)}>
          <div className="trade-modal" onClick={e => e.stopPropagation()}>
            <div className="trade-modal-header">
              <h2>New Strategy</h2>
              <button className="trade-modal-close" onClick={() => setStratModalOpen(false)}>&times;</button>
            </div>
            <form onSubmit={handleCreateStrategy} className="trade-modal-form">
              <div className="trade-field">
                <label>Title</label>
                <input
                  type="text"
                  value={newStrat.title}
                  onChange={e => setNewStrat(s => ({ ...s, title: e.target.value }))}
                  placeholder="e.g. Momentum Plays"
                  required
                  autoFocus
                />
              </div>
              <div className="trade-field">
                <label>Description (optional)</label>
                <textarea
                  value={newStrat.description}
                  onChange={e => setNewStrat(s => ({ ...s, description: e.target.value }))}
                  placeholder="What's the thesis?"
                  rows={2}
                />
              </div>
              <div className="trade-field">
                <label>Color</label>
                <div className="trading-color-picker">
                  {['#6366f1', '#f472b6', '#34d399', '#fbbf24', '#60a5fa', '#a78bfa', '#f87171', '#2dd4bf'].map(c => (
                    <button
                      key={c}
                      type="button"
                      className={`trading-color-swatch ${newStrat.color === c ? 'active' : ''}`}
                      style={{ background: c }}
                      onClick={() => setNewStrat(s => ({ ...s, color: c }))}
                    />
                  ))}
                </div>
              </div>
              <button type="submit" className="trade-submit-btn" disabled={stratSubmitting || !newStrat.title.trim()}>
                {stratSubmitting ? 'Creating...' : 'Create Strategy'}
              </button>
            </form>
          </div>
        </div>
      )}

      <TradeEntryModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onTradeOpened={fetchPortfolio}
      />
    </div>
  )
}
