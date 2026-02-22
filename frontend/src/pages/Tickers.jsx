import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiSearch, FiTrendingUp } from 'react-icons/fi'
import { get } from '../api/client'
import './Tickers.css'

export default function Tickers() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [trending, setTrending] = useState(null)

  // Load trending on mount as default content
  useEffect(() => {
    get('/tickers/trending?window=24h&limit=30')
      .then((res) => setTrending(res.tickers))
      .catch(() => {})
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

  const displayList = results ?? trending
  const showingSearch = results !== null

  return (
    <div className="tickers-page">
      <div className="tickers-header">
        <h1>Tickers</h1>
        <div className="tickers-search-wrap">
          <FiSearch className="tickers-search-icon" />
          <input
            type="text"
            className="tickers-search"
            placeholder="Search tickers..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
        </div>
      </div>

      {!showingSearch && trending && (
        <div className="tickers-section-label">
          <FiTrendingUp /> Trending (24h)
        </div>
      )}

      {showingSearch && results?.length === 0 && (
        <p className="tickers-empty">No tickers matching "{query}"</p>
      )}

      {loading && !displayList && <p className="tickers-loading">Searching...</p>}

      {displayList && displayList.length > 0 && (
        <div className="tickers-list">
          {displayList.map((t) => (
            <div
              key={t.ticker}
              className="tickers-row"
              onClick={() => navigate(`/tickers/${t.ticker}`)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && navigate(`/tickers/${t.ticker}`)}
            >
              <span className="tickers-row-symbol">{t.ticker}</span>
              <span className="tickers-row-count">{t.mention_count.toLocaleString()} mentions</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
