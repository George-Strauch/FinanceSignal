import { useState, useEffect, useRef, useCallback } from 'react'
import { get, post } from '../api/client'
import './TradeEntryModal.css'

export default function TradeEntryModal({ isOpen, onClose, onTradeOpened, prefillTicker, prefillPrice }) {
  const [ticker, setTicker] = useState(prefillTicker || '')
  const [direction, setDirection] = useState('long')
  const [strategyId, setStrategyId] = useState('')
  const [price, setPrice] = useState(prefillPrice || '')
  const [note, setNote] = useState('')
  const [strategies, setStrategies] = useState([])
  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const tickerRef = useRef(null)
  const suggestRef = useRef(null)

  useEffect(() => {
    if (isOpen) {
      setTicker(prefillTicker || '')
      setPrice(prefillPrice || '')
      setDirection('long')
      setNote('')
      setError(null)
      get('/trading/strategies?status=active')
        .then(res => {
          setStrategies(res.strategies || [])
          if (res.strategies?.length > 0 && !strategyId) {
            setStrategyId(String(res.strategies[0].id))
          }
        })
        .catch(() => {})
      if (!prefillTicker) {
        setTimeout(() => tickerRef.current?.focus(), 100)
      }
    }
  }, [isOpen, prefillTicker, prefillPrice])

  const searchTicker = useCallback(async (q) => {
    if (q.length < 1) { setSuggestions([]); return }
    try {
      const res = await get(`/tickers/search?q=${encodeURIComponent(q)}&limit=8`)
      setSuggestions(res.results || [])
      setShowSuggestions(true)
    } catch {
      setSuggestions([])
    }
  }, [])

  useEffect(() => {
    const handler = (e) => {
      if (suggestRef.current && !suggestRef.current.contains(e.target)) setShowSuggestions(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleTickerChange = (e) => {
    const v = e.target.value.toUpperCase()
    setTicker(v)
    searchTicker(v)
  }

  const selectTicker = async (t) => {
    setTicker(t)
    setShowSuggestions(false)
    // Auto-fill price
    try {
      const res = await get(`/fundamentals/${t}`)
      if (res.current_price) setPrice(String(res.current_price))
    } catch {}
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!ticker || !price || !strategyId) return
    setSubmitting(true)
    setError(null)
    try {
      const trade = await post('/trading/trades', {
        strategy_id: Number(strategyId),
        ticker: ticker.toUpperCase(),
        direction,
        entry_price: Number(price),
        entry_note: note,
      })
      onTradeOpened?.(trade)
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (!isOpen) return null

  return (
    <div className="trade-modal-backdrop" onClick={onClose}>
      <div className="trade-modal" onClick={e => e.stopPropagation()}>
        <div className="trade-modal-header">
          <h2>Open Trade</h2>
          <button className="trade-modal-close" onClick={onClose}>&times;</button>
        </div>
        <form onSubmit={handleSubmit} className="trade-modal-form">
          <div className="trade-field" ref={suggestRef}>
            <label>Ticker</label>
            <input
              ref={tickerRef}
              type="text"
              value={ticker}
              onChange={handleTickerChange}
              onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
              placeholder="AAPL"
              required
              disabled={!!prefillTicker}
            />
            {showSuggestions && suggestions.length > 0 && (
              <div className="trade-suggestions">
                {suggestions.map(s => (
                  <button key={s.ticker} type="button" onClick={() => selectTicker(s.ticker)}>
                    <span className="trade-suggest-ticker">{s.ticker}</span>
                    <span className="trade-suggest-count">{s.mention_count} mentions</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="trade-field">
            <label>Direction</label>
            <div className="trade-direction-toggle">
              <button
                type="button"
                className={`trade-dir-btn ${direction === 'long' ? 'active long' : ''}`}
                onClick={() => setDirection('long')}
              >
                Long
              </button>
              <button
                type="button"
                className={`trade-dir-btn ${direction === 'short' ? 'active short' : ''}`}
                onClick={() => setDirection('short')}
              >
                Short
              </button>
            </div>
          </div>

          <div className="trade-field">
            <label>Strategy</label>
            <select value={strategyId} onChange={e => setStrategyId(e.target.value)} required>
              <option value="">Select strategy...</option>
              {strategies.map(s => (
                <option key={s.id} value={s.id}>{s.title}</option>
              ))}
            </select>
          </div>

          <div className="trade-field">
            <label>Entry Price ($)</label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={price}
              onChange={e => setPrice(e.target.value)}
              placeholder="0.00"
              required
            />
          </div>

          <div className="trade-field">
            <label>Note (optional)</label>
            <textarea
              value={note}
              onChange={e => setNote(e.target.value)}
              placeholder="Thesis or rationale..."
              rows={2}
            />
          </div>

          {error && <div className="trade-modal-error">{error}</div>}

          <button type="submit" className="trade-submit-btn" disabled={submitting || !ticker || !price || !strategyId}>
            {submitting ? 'Opening...' : `Open ${direction.charAt(0).toUpperCase() + direction.slice(1)} Position`}
          </button>
        </form>
      </div>
    </div>
  )
}
