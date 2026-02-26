import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { FiArrowLeft } from 'react-icons/fi'
import { get, put, del } from '../api/client'
import TradeEntryModal from '../components/TradeEntryModal'
import PositionsTable from '../components/PositionsTable'
import './StrategyDetail.css'

function formatPct(v) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function formatDuration(seconds) {
  if (seconds == null) return '-'
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`
  return `${(seconds / 86400).toFixed(1)}d`
}

function formatTs(epoch) {
  if (!epoch) return ''
  const d = new Date(epoch * 1000)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function StrategyDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('open')
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [editNotes, setEditNotes] = useState('')
  const [editColor, setEditColor] = useState('')
  const [modalOpen, setModalOpen] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await get(`/trading/strategies/${id}/performance`)
      setData(res)
      setEditTitle(res.strategy.title)
      setEditDesc(res.strategy.description)
      setEditNotes(res.strategy.notes)
      setEditColor(res.strategy.color)
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { fetchData() }, [fetchData])

  const handleSave = async () => {
    try {
      await put(`/trading/strategies/${id}`, {
        title: editTitle,
        description: editDesc,
        notes: editNotes,
        color: editColor,
      })
      setEditing(false)
      fetchData()
    } catch {}
  }

  const handleArchive = async () => {
    try {
      await del(`/trading/strategies/${id}`)
      navigate('/trading')
    } catch {}
  }

  if (loading && !data) {
    return <div className="sd-loading">Loading strategy...</div>
  }

  if (!data) {
    return <div className="sd-error">Strategy not found.</div>
  }

  const { strategy, stats, trades, equity_curve } = data
  const isBotManaged = !!strategy.bot_id
  const openTrades = trades.filter(t => t.status === 'open')
  const closedTrades = trades.filter(t => t.status === 'closed')
  const displayTrades = tab === 'open' ? openTrades : tab === 'closed' ? closedTrades : trades

  // Enrich trades with strategy title
  for (const t of trades) t.strategy_title = strategy.title

  const equityData = equity_curve.map(s => ({
    t: formatTs(s.snapshot_at),
    avg_return: s.avg_return_pct,
  }))

  return (
    <div className="sd-page">
      <div className="sd-header">
        <button className="sd-back" onClick={() => navigate('/trading')}>
          <FiArrowLeft />
        </button>
        <div className="sd-header-info">
          {editing ? (
            <div className="sd-edit-form">
              <div className="sd-edit-row">
                <input
                  className="sd-edit-title"
                  value={editTitle}
                  onChange={e => setEditTitle(e.target.value)}
                  placeholder="Strategy title"
                />
                <input
                  type="color"
                  className="sd-edit-color"
                  value={editColor}
                  onChange={e => setEditColor(e.target.value)}
                />
              </div>
              <textarea
                className="sd-edit-desc"
                value={editDesc}
                onChange={e => setEditDesc(e.target.value)}
                placeholder="Description..."
                rows={2}
              />
              <textarea
                className="sd-edit-notes"
                value={editNotes}
                onChange={e => setEditNotes(e.target.value)}
                placeholder="Notes..."
                rows={2}
              />
              <div className="sd-edit-actions">
                <button className="sd-save-btn" onClick={handleSave}>Save</button>
                <button className="sd-cancel-btn" onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <>
              <div className="sd-title-row">
                <span className="sd-color-dot" style={{ background: strategy.color }} />
                <h1>{strategy.title}</h1>
                <span className={`sd-status-badge ${strategy.status}`}>{strategy.status}</span>
              </div>
              {strategy.description && <p className="sd-description">{strategy.description}</p>}
              {strategy.notes && <p className="sd-notes">{strategy.notes}</p>}
            </>
          )}
        </div>
        <div className="sd-header-actions">
          {!editing && !isBotManaged && (
            <>
              <button className="sd-action-btn" onClick={() => setModalOpen(true)}>+ Trade</button>
              <button className="sd-action-btn" onClick={() => setEditing(true)}>Edit</button>
              {strategy.status === 'active' && (
                <button className="sd-action-btn sd-archive-btn" onClick={handleArchive}>Archive</button>
              )}
            </>
          )}
          {isBotManaged && (
            <button className="sd-action-btn" onClick={() => navigate(`/trading/bots/${strategy.bot_id}`)}>
              View Bot
            </button>
          )}
        </div>
      </div>

      {/* Bot Managed Banner */}
      {isBotManaged && (
        <div className="sd-bot-banner">
          This strategy is managed by an automated trading bot. Trades are opened and closed automatically.
        </div>
      )}

      {/* Performance Stats Grid */}
      <div className="sd-stats-grid">
        <div className="sd-stat">
          <span className={stats.win_rate != null ? (stats.win_rate >= 0.5 ? 'pnl-positive' : 'pnl-negative') : ''}>
            {stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '-'}
          </span>
          <span className="sd-stat-label">Win Rate</span>
        </div>
        <div className="sd-stat">
          <span>{stats.profit_factor != null ? stats.profit_factor.toFixed(2) : '-'}</span>
          <span className="sd-stat-label">Profit Factor</span>
        </div>
        <div className="sd-stat">
          <span className={stats.avg_win_pct != null ? 'pnl-positive' : ''}>{formatPct(stats.avg_win_pct)}</span>
          <span className="sd-stat-label">Avg Win</span>
        </div>
        <div className="sd-stat">
          <span className={stats.avg_loss_pct != null ? 'pnl-negative' : ''}>{formatPct(stats.avg_loss_pct)}</span>
          <span className="sd-stat-label">Avg Loss</span>
        </div>
        <div className="sd-stat">
          <span>{stats.total_trades}</span>
          <span className="sd-stat-label">Total Trades</span>
        </div>
        <div className="sd-stat">
          <span>{formatDuration(stats.avg_holding_seconds)}</span>
          <span className="sd-stat-label">Avg Hold Time</span>
        </div>
        <div className="sd-stat">
          <span className="pnl-positive">{formatPct(stats.best_trade_pct)}</span>
          <span className="sd-stat-label">Best Trade</span>
        </div>
        <div className="sd-stat">
          <span className="pnl-negative">{formatPct(stats.worst_trade_pct)}</span>
          <span className="sd-stat-label">Worst Trade</span>
        </div>
      </div>

      {/* Equity Curve */}
      {equityData.length > 1 && (
        <div className="sd-section">
          <h2>Equity Curve</h2>
          <div className="sd-chart">
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={equityData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(var(--soft-border), 0.3)" />
                <XAxis
                  dataKey="t"
                  tick={{ fill: 'rgb(var(--soft-text))', fontSize: 11 }}
                  stroke="rgba(var(--soft-border), 0.5)"
                  minTickGap={40}
                />
                <YAxis
                  tickFormatter={v => `${v}%`}
                  tick={{ fill: 'rgb(var(--soft-text))', fontSize: 11 }}
                  stroke="rgba(var(--soft-border), 0.5)"
                />
                <Tooltip
                  contentStyle={{
                    background: 'rgb(var(--primary-color))',
                    border: '1px solid rgba(var(--soft-border), var(--soft-border-alpha))',
                    borderRadius: 8,
                    fontSize: '0.82rem',
                  }}
                  formatter={v => [`${v?.toFixed(2)}%`, 'Avg Return']}
                />
                <Line
                  type="monotone"
                  dataKey="avg_return"
                  stroke={strategy.color}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Trades Table */}
      <div className="sd-section">
        <div className="sd-trades-header">
          <div className="sd-tabs">
            <button className={`sd-tab ${tab === 'open' ? 'active' : ''}`} onClick={() => setTab('open')}>
              Open ({openTrades.length})
            </button>
            <button className={`sd-tab ${tab === 'closed' ? 'active' : ''}`} onClick={() => setTab('closed')}>
              Closed ({closedTrades.length})
            </button>
            <button className={`sd-tab ${tab === 'all' ? 'active' : ''}`} onClick={() => setTab('all')}>
              All ({trades.length})
            </button>
          </div>
        </div>
        <PositionsTable
          trades={displayTrades}
          showStrategy={false}
          showClose={tab !== 'closed'}
          onTradeUpdated={fetchData}
        />
      </div>

      <TradeEntryModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onTradeOpened={fetchData}
      />
    </div>
  )
}
