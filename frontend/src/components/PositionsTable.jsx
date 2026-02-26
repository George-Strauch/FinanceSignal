import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { post } from '../api/client'
import './PositionsTable.css'

function formatDuration(seconds) {
  if (seconds == null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`
  return `${(seconds / 86400).toFixed(1)}d`
}

function formatPrice(v) {
  if (v == null) return '-'
  return `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatPnl(v) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

export default function PositionsTable({ trades, showStrategy = true, showClose = true, onTradeUpdated, compact = false }) {
  const navigate = useNavigate()
  const [closingId, setClosingId] = useState(null)
  const [closePrice, setClosePrice] = useState('')
  const [closeNote, setCloseNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleClose = async (tradeId) => {
    if (!closePrice) return
    setSubmitting(true)
    try {
      await post(`/trading/trades/${tradeId}/close`, {
        exit_price: Number(closePrice),
        exit_note: closeNote,
      })
      setClosingId(null)
      setClosePrice('')
      setCloseNote('')
      onTradeUpdated?.()
    } catch {
    } finally {
      setSubmitting(false)
    }
  }

  const startClose = (trade) => {
    setClosingId(trade.id)
    setClosePrice(trade.current_price ? String(trade.current_price) : '')
    setCloseNote('')
  }

  if (!trades || trades.length === 0) {
    return <p className="positions-empty">No positions to display.</p>
  }

  return (
    <div className={`positions-table-wrap ${compact ? 'compact' : ''}`}>
      <table className="positions-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Dir</th>
            {showStrategy && <th>Strategy</th>}
            <th>Entry</th>
            <th>{trades[0]?.status === 'open' ? 'Current' : 'Exit'}</th>
            <th>P&L %</th>
            <th>Duration</th>
            {showClose && <th></th>}
          </tr>
        </thead>
        <tbody>
          {trades.map(t => {
            const isOpen = t.status === 'open'
            const pnl = isOpen ? t.unrealized_pnl_pct : t.realized_pnl_pct
            const pnlClass = pnl != null ? (pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : ''
            const curPrice = isOpen ? t.current_price : t.exit_price
            const duration = isOpen ? t.holding_seconds : t.holding_seconds

            return closingId === t.id ? (
              <tr key={t.id} className="positions-close-row">
                <td colSpan={showStrategy ? (showClose ? 8 : 7) : (showClose ? 7 : 6)}>
                  <div className="positions-close-form">
                    <span className="positions-close-label">Close {t.ticker} {t.direction}:</span>
                    <input
                      type="number"
                      step="0.01"
                      min="0.01"
                      placeholder="Exit price"
                      value={closePrice}
                      onChange={e => setClosePrice(e.target.value)}
                      autoFocus
                    />
                    <input
                      type="text"
                      placeholder="Note (optional)"
                      value={closeNote}
                      onChange={e => setCloseNote(e.target.value)}
                    />
                    <button
                      className="positions-close-confirm"
                      onClick={() => handleClose(t.id)}
                      disabled={submitting || !closePrice}
                    >
                      {submitting ? '...' : 'Confirm'}
                    </button>
                    <button className="positions-close-cancel" onClick={() => setClosingId(null)}>Cancel</button>
                  </div>
                </td>
              </tr>
            ) : (
              <tr key={t.id}>
                <td>
                  <button className="positions-ticker-link" onClick={() => navigate(`/tickers/${t.ticker}`)}>
                    {t.ticker}
                  </button>
                </td>
                <td>
                  <span className={`positions-dir ${t.direction}`}>
                    {t.direction === 'long' ? 'LONG' : 'SHORT'}
                  </span>
                </td>
                {showStrategy && (
                  <td>
                    <button className="positions-strat-link" onClick={() => navigate(`/trading/strategies/${t.strategy_id}`)}>
                      {t.strategy_title || `#${t.strategy_id}`}
                    </button>
                  </td>
                )}
                <td className="positions-price">{formatPrice(t.entry_price)}</td>
                <td className="positions-price">{formatPrice(curPrice)}</td>
                <td className={`positions-pnl ${pnlClass}`}>{formatPnl(pnl)}</td>
                <td className="positions-duration">{formatDuration(duration)}</td>
                {showClose && isOpen && (
                  <td>
                    <button className="positions-close-btn" onClick={() => startClose(t)}>Close</button>
                  </td>
                )}
                {showClose && !isOpen && <td></td>}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
