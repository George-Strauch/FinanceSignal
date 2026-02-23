import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiSearch, FiClock } from 'react-icons/fi'
import { get } from '../api/client'
import './Tickers.css'

const RECENT_KEY = 'recent-ticker-visits'
const MAX_RECENT = 25

function getRecentTickers() {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
  } catch {
    return []
  }
}

export function recordTickerVisit(ticker) {
  const recent = getRecentTickers().filter((t) => t !== ticker)
  recent.unshift(ticker)
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)))
}

export default function Tickers() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [recentTickers, setRecentTickers] = useState([])

  useEffect(() => {
    setRecentTickers(getRecentTickers())
  }, [])

  const search = useCallback(async (q) => {
    if (!q.trim()) {
      setResults(null)
      return
    }
    setLoading(true)
    try {
      const res = await get(`/tickers/search?q=${encodeURIComponent(q)}&limit=50`)
      setResults(res.results)
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => search(query), 300)
    return () => clearTimeout(timer)
  }, [query, search])

  const goToTicker = (ticker) => {
    recordTickerVisit(ticker)
    navigate(`/tickers/${ticker}`)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && query.trim()) {
      const symbol = query.trim().toUpperCase()
      goToTicker(symbol)
    }
  }

  const showingSearch = results !== null

  return (
    <div className="tickers-page">
      <div className="tickers-search-hero">
        <h1>Tickers</h1>
        <div className="tickers-search-wrap">
          <FiSearch className="tickers-search-icon" />
          <input
            type="text"
            className="tickers-search"
            placeholder="Search tickers... press Enter to go"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            autoFocus
          />
        </div>
      </div>

      {showingSearch && results?.length === 0 && (
        <p className="tickers-empty">No tickers matching "{query}"</p>
      )}

      {loading && !results && <p className="tickers-loading">Searching...</p>}

      {showingSearch && results?.length > 0 && (
        <div className="tickers-results-list">
          {results.map((t) => (
            <div
              key={t.ticker}
              className="tickers-result-row"
              onClick={() => goToTicker(t.ticker)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && goToTicker(t.ticker)}
            >
              <span className="tickers-row-symbol">
                {t.ticker}
                {t.tags?.map((tag) => (
                  <span key={tag.id} className="tag-chip" style={{ backgroundColor: tag.color, marginLeft: 6 }}>
                    {tag.name}
                  </span>
                ))}
              </span>
              <span className="tickers-row-count">{t.mention_count.toLocaleString()} mentions</span>
            </div>
          ))}
        </div>
      )}

      {!showingSearch && recentTickers.length > 0 && (
        <>
          <div className="tickers-section-label">
            <FiClock /> Recently Visited
          </div>
          <div className="tickers-recent-grid">
            {recentTickers.map((ticker) => (
              <div
                key={ticker}
                className="ticker-recent-card"
                onClick={() => goToTicker(ticker)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && goToTicker(ticker)}
              >
                <span className="ticker-recent-symbol">{ticker}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {!showingSearch && recentTickers.length === 0 && (
        <div className="tickers-empty-state">
          <FiSearch className="tickers-empty-icon" />
          <p>Search for a ticker above to get started.</p>
          <p className="tickers-empty-hint">Recently visited tickers will appear here.</p>
        </div>
      )}
    </div>
  )
}
