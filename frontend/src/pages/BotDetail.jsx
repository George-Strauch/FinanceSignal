import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { FiArrowLeft } from 'react-icons/fi'
import { get, post } from '../api/client'
import PositionsTable from '../components/PositionsTable'
import './BotDetail.css'

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

function defaultDates() {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - 30)
  return {
    start: start.toISOString().split('T')[0],
    end: end.toISOString().split('T')[0],
  }
}

export default function BotDetail() {
  const { botId } = useParams()
  const navigate = useNavigate()
  const [bot, setBot] = useState(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('open')
  const [btDates, setBtDates] = useState(defaultDates)
  const [btStatus, setBtStatus] = useState(null)
  const [btPolling, setBtPolling] = useState(false)
  const pollRef = useRef(null)

  const fetchBot = useCallback(async () => {
    setLoading(true)
    try {
      const res = await get(`/bots/${botId}`)
      setBot(res)
    } catch {
      setBot(null)
    } finally {
      setLoading(false)
    }
  }, [botId])

  useEffect(() => { fetchBot() }, [fetchBot])

  // Backtest polling
  useEffect(() => {
    if (!btPolling) return
    pollRef.current = setInterval(async () => {
      try {
        const res = await get(`/bots/${botId}/backtest/status`)
        setBtStatus(res)
        if (res.status !== 'running') {
          setBtPolling(false)
          fetchBot()
        }
      } catch {
        setBtPolling(false)
      }
    }, 2000)
    return () => clearInterval(pollRef.current)
  }, [btPolling, botId, fetchBot])

  const handleToggleLive = async () => {
    try {
      await post(`/bots/${botId}/toggle-live`)
      fetchBot()
    } catch {}
  }

  const handleStartBacktest = async () => {
    try {
      const res = await post(`/bots/${botId}/backtest`, {
        start_date: btDates.start,
        end_date: btDates.end,
      })
      setBtStatus({ status: 'running', run: { id: res.run_id } })
      setBtPolling(true)
    } catch {}
  }

  const handleStopBacktest = async () => {
    try {
      await post(`/bots/${botId}/backtest/stop`)
      setBtPolling(false)
      fetchBot()
    } catch {}
  }

  if (loading && !bot) return <div className="bd-loading">Loading bot...</div>
  if (!bot) return <div className="bd-error">Bot not found.</div>

  const stats = bot.stats || {}
  const trades = bot.trades || []
  const openTrades = trades.filter(t => t.status === 'open')
  const closedTrades = trades.filter(t => t.status === 'closed')
  const displayTrades = tab === 'open' ? openTrades : tab === 'closed' ? closedTrades : trades
  const equityData = (bot.equity_curve || []).map(s => ({
    t: formatTs(s.snapshot_at),
    avg_return: s.avg_return_pct,
  }))
  const backtests = bot.backtests || []
  const latestBt = backtests[0]
  const btRun = btStatus?.run || latestBt
  const isRunning = btStatus?.status === 'running'
  const btProgress = btRun && btRun.total_hours > 0
    ? Math.round((btRun.hours_evaluated / btRun.total_hours) * 100)
    : 0

  return (
    <div className="bd-page">
      {/* Header */}
      <div className="bd-header">
        <button className="bd-back" onClick={() => navigate('/trading/bots')}>
          <FiArrowLeft />
        </button>
        <div className="bd-header-info">
          <div className="bd-title-row">
            <span className="bd-color-dot" style={{ background: bot.color }} />
            <h1>{bot.name}</h1>
          </div>
          <p className="bd-description">{bot.description}</p>
        </div>
        <div className="bd-header-actions">
          <button
            className={`bot-live-toggle ${bot.live_trading ? 'active' : ''}`}
            onClick={handleToggleLive}
          >
            <span className="bot-live-dot" />
            {bot.live_trading ? 'LIVE' : 'OFF'}
          </button>
        </div>
      </div>

      {/* Performance Stats */}
      <div className="bd-stats-grid">
        <div className="bd-stat">
          <span className={stats.win_rate != null ? (stats.win_rate >= 0.5 ? 'pnl-positive' : 'pnl-negative') : ''}>
            {stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '-'}
          </span>
          <span className="bd-stat-label">Win Rate</span>
        </div>
        <div className="bd-stat">
          <span>{stats.profit_factor != null ? stats.profit_factor.toFixed(2) : '-'}</span>
          <span className="bd-stat-label">Profit Factor</span>
        </div>
        <div className="bd-stat">
          <span className={stats.avg_win_pct != null ? 'pnl-positive' : ''}>{formatPct(stats.avg_win_pct)}</span>
          <span className="bd-stat-label">Avg Win</span>
        </div>
        <div className="bd-stat">
          <span className={stats.avg_loss_pct != null ? 'pnl-negative' : ''}>{formatPct(stats.avg_loss_pct)}</span>
          <span className="bd-stat-label">Avg Loss</span>
        </div>
        <div className="bd-stat">
          <span>{stats.total_trades ?? 0}</span>
          <span className="bd-stat-label">Total Trades</span>
        </div>
        <div className="bd-stat">
          <span>{formatDuration(stats.avg_holding_seconds)}</span>
          <span className="bd-stat-label">Avg Hold Time</span>
        </div>
        <div className="bd-stat">
          <span className="pnl-positive">{formatPct(stats.best_trade_pct)}</span>
          <span className="bd-stat-label">Best Trade</span>
        </div>
        <div className="bd-stat">
          <span className="pnl-negative">{formatPct(stats.worst_trade_pct)}</span>
          <span className="bd-stat-label">Worst Trade</span>
        </div>
      </div>

      {/* Equity Curve */}
      {equityData.length > 1 && (
        <div className="bd-section">
          <h2>Equity Curve</h2>
          <div className="bd-chart">
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={equityData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(var(--soft-border), 0.3)" />
                <XAxis dataKey="t" tick={{ fill: 'rgb(var(--soft-text))', fontSize: 11 }} stroke="rgba(var(--soft-border), 0.5)" minTickGap={40} />
                <YAxis tickFormatter={v => `${v}%`} tick={{ fill: 'rgb(var(--soft-text))', fontSize: 11 }} stroke="rgba(var(--soft-border), 0.5)" />
                <Tooltip
                  contentStyle={{
                    background: 'rgb(var(--primary-color))',
                    border: '1px solid rgba(var(--soft-border), var(--soft-border-alpha))',
                    borderRadius: 8, fontSize: '0.82rem',
                  }}
                  formatter={v => [`${v?.toFixed(2)}%`, 'Avg Return']}
                />
                <Line type="monotone" dataKey="avg_return" stroke={bot.color} strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Backtest Section */}
      <div className="bd-section">
        <h2>Backtest</h2>
        <div className="bd-backtest-controls">
          <div className="bd-backtest-dates">
            <label>
              Start
              <input type="date" value={btDates.start} onChange={e => setBtDates(d => ({ ...d, start: e.target.value }))} />
            </label>
            <label>
              End
              <input type="date" value={btDates.end} onChange={e => setBtDates(d => ({ ...d, end: e.target.value }))} />
            </label>
          </div>
          {isRunning ? (
            <button className="bd-backtest-stop-btn" onClick={handleStopBacktest}>Stop</button>
          ) : (
            <button className="bd-backtest-run-btn" onClick={handleStartBacktest}>Run Backtest</button>
          )}
        </div>

        {/* Progress */}
        {isRunning && btRun && (
          <div className="bd-backtest-progress">
            <div className="bd-progress-bar">
              <div className="bd-progress-fill" style={{ width: `${btProgress}%` }} />
            </div>
            <span className="bd-progress-text">
              {btRun.hours_evaluated || 0} / {btRun.total_hours || '?'} hours ({btProgress}%)
              {btRun.trades_generated > 0 && ` — ${btRun.trades_generated} trades`}
            </span>
          </div>
        )}

        {/* Latest backtest result */}
        {latestBt && latestBt.status === 'completed' && (
          <div className="bd-backtest-result">
            <div className="bd-backtest-result-stats">
              <span><strong>{latestBt.total_trades}</strong> trades</span>
              <span>Win rate: <strong>{latestBt.win_rate != null ? `${(latestBt.win_rate * 100).toFixed(1)}%` : '-'}</strong></span>
              <span>Avg return: <strong>{formatPct(latestBt.avg_return_pct)}</strong></span>
              <span>{latestBt.start_date} to {latestBt.end_date}</span>
            </div>
          </div>
        )}

        {latestBt && latestBt.status === 'failed' && (
          <div className="bd-backtest-error">
            Backtest failed: {latestBt.error || 'Unknown error'}
          </div>
        )}
      </div>

      {/* Trades Table */}
      <div className="bd-section">
        <div className="bd-trades-header">
          <div className="bd-tabs">
            <button className={`bd-tab ${tab === 'open' ? 'active' : ''}`} onClick={() => setTab('open')}>
              Open ({openTrades.length})
            </button>
            <button className={`bd-tab ${tab === 'closed' ? 'active' : ''}`} onClick={() => setTab('closed')}>
              Closed ({closedTrades.length})
            </button>
            <button className={`bd-tab ${tab === 'all' ? 'active' : ''}`} onClick={() => setTab('all')}>
              All ({trades.length})
            </button>
          </div>
        </div>
        <PositionsTable
          trades={displayTrades}
          showStrategy={false}
          showClose={false}
          onTradeUpdated={fetchBot}
        />
      </div>
    </div>
  )
}
