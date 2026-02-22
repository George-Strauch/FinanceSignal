import { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { AreaChart, Area, ResponsiveContainer } from 'recharts'
import { FiGrid, FiList, FiRefreshCw, FiTrendingUp, FiTrendingDown, FiMinus, FiBarChart, FiArrowRight, FiFilter } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import './TrendingDashboard.css'

const WINDOWS = ['1h', '6h', '24h', '7d']
const REFRESH_INTERVAL = 60

function SparklineChart({ data, id }) {
  if (!data || data.length === 0) return null
  const gradientId = `spark-grad-${id}`
  return (
    <ResponsiveContainer width={100} height={32}>
      <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--accent))" stopOpacity={0.4} />
            <stop offset="100%" stopColor="rgb(var(--accent))" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="v"
          stroke="rgb(var(--accent))"
          strokeWidth={1.5}
          fill={`url(#${gradientId})`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function TrendIcon({ trend }) {
  if (trend === 'up') return <FiTrendingUp className="trend-indicator up" />
  if (trend === 'down') return <FiTrendingDown className="trend-indicator down" />
  return <FiMinus className="trend-indicator flat" />
}

function SentimentBadge({ sentiment }) {
  if (!sentiment) return null
  const { label } = sentiment
  const icon = label === 'bullish' ? <FiTrendingUp /> : label === 'bearish' ? <FiTrendingDown /> : <FiArrowRight />
  return (
    <span className={`sentiment-badge sentiment-${label}`}>
      {icon} {label}
    </span>
  )
}

function TickerCard({ ticker, onClick }) {
  return (
    <div className="ticker-card" onClick={onClick} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onClick()}>
      <div className="ticker-card-header">
        <span className="ticker-symbol">{ticker.ticker}</span>
        <TrendIcon trend={ticker.trend} />
      </div>
      <SentimentBadge sentiment={ticker.sentiment} />
      <div className="ticker-card-mentions">{ticker.mention_count.toLocaleString()} mentions</div>
      <div className="ticker-card-sparkline">
        <SparklineChart data={ticker.sparkline} id={ticker.ticker} />
      </div>
      {ticker.tags?.length > 0 && (
        <div className="ticker-card-tags">
          {ticker.tags.map((tag) => (
            <span key={tag.id} className="tag-chip" style={{ backgroundColor: tag.color }}>
              {tag.name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SkeletonCards() {
  return (
    <div className="ticker-grid">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="ticker-card skeleton">
          <div className="skel-line skel-title" />
          <div className="skel-line skel-count" />
          <div className="skel-line skel-chart" />
          <div className="skel-line skel-chips" />
        </div>
      ))}
    </div>
  )
}

export default function TrendingDashboard() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [window, setWindow] = usePersistedState('trending-window', '24h')
  const [viewMode, setViewMode] = usePersistedState('trending-view', 'cards')
  const [autoRefresh, setAutoRefresh] = usePersistedState('trending-autorefresh', false)
  const [countdown, setCountdown] = useState(REFRESH_INTERVAL)
  const [sortKey, setSortKey] = useState('mention_count')
  const [sortDir, setSortDir] = useState('desc')
  const [refreshing, setRefreshing] = useState(false)
  const [hiddenTags, setHiddenTags] = usePersistedState('trending-hidden-tags', [])
  const [allTagSets, setAllTagSets] = useState([])
  const [filterOpen, setFilterOpen] = useState(false)
  const filterRef = useRef(null)
  const intervalRef = useRef(null)

  const fetchData = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)
    try {
      const res = await get(`/tickers/trending?window=${window}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [window])

  // Fetch on mount + window change
  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Fetch tag sets for filter
  useEffect(() => {
    get('/ticker-tags').then((res) => setAllTagSets(res.tag_sets)).catch(() => {})
  }, [])

  // Close filter dropdown on click outside
  useEffect(() => {
    const handleClick = (e) => {
      if (filterRef.current && !filterRef.current.contains(e.target)) setFilterOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  // Auto-refresh
  useEffect(() => {
    if (!autoRefresh) {
      setCountdown(REFRESH_INTERVAL)
      if (intervalRef.current) clearInterval(intervalRef.current)
      return
    }
    setCountdown(REFRESH_INTERVAL)
    intervalRef.current = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          fetchData(true)
          return REFRESH_INTERVAL
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(intervalRef.current)
  }, [autoRefresh, fetchData])

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const toggleTag = (tagId) => {
    setHiddenTags((prev) =>
      prev.includes(tagId) ? prev.filter((id) => id !== tagId) : [...prev, tagId]
    )
  }

  const filteredTickers = data?.tickers
    ? data.tickers.filter((t) => {
        if (hiddenTags.length === 0) return true
        return !t.tags?.some((tag) => hiddenTags.includes(tag.id))
      })
    : []

  const sortedTickers = filteredTickers.length > 0
    ? [...filteredTickers].sort((a, b) => {
        let aVal = a[sortKey]
        let bVal = b[sortKey]
        if (sortKey === 'ticker') {
          aVal = aVal.toLowerCase()
          bVal = bVal.toLowerCase()
          return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
        }
        return sortDir === 'asc' ? aVal - bVal : bVal - aVal
      })
    : filteredTickers

  const sortIndicator = (key) => {
    if (sortKey !== key) return ''
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC'
  }

  const isEmpty = !loading && data?.tickers?.length === 0
  const showSkeleton = loading && !data

  return (
    <div className="trending-dashboard">
      <div className="trending-header">
        <div className="trending-title-row">
          <h1>Trending Tickers</h1>
          {refreshing && <span className="refresh-indicator">Updating...</span>}
        </div>
        <div className="trending-controls">
          <div className="window-selector">
            {WINDOWS.map((w) => (
              <button
                key={w}
                className={`window-btn ${window === w ? 'active' : ''}`}
                onClick={() => setWindow(w)}
              >
                {w}
              </button>
            ))}
          </div>
          <div className="view-toggle">
            <button
              className={`toggle-btn ${viewMode === 'cards' ? 'active' : ''}`}
              onClick={() => setViewMode('cards')}
              title="Card view"
            >
              <FiGrid />
            </button>
            <button
              className={`toggle-btn ${viewMode === 'table' ? 'active' : ''}`}
              onClick={() => setViewMode('table')}
              title="Table view"
            >
              <FiList />
            </button>
          </div>
          <button
            className={`toggle-btn auto-refresh ${autoRefresh ? 'active' : ''}`}
            onClick={() => setAutoRefresh((v) => !v)}
            title={autoRefresh ? 'Disable auto-refresh' : 'Enable auto-refresh'}
          >
            <FiRefreshCw />
            {autoRefresh && <span className="countdown">{countdown}s</span>}
          </button>
          {allTagSets.length > 0 && (
            <div className="tag-filter-wrap" ref={filterRef}>
              <button
                className={`toggle-btn tag-filter-btn ${hiddenTags.length > 0 ? 'active' : ''}`}
                onClick={() => setFilterOpen((v) => !v)}
                title="Filter by tags"
              >
                <FiFilter />
                {hiddenTags.length > 0 && <span className="tag-filter-count">{hiddenTags.length}</span>}
              </button>
              {filterOpen && (
                <div className="tag-filter-dropdown">
                  <div className="tag-filter-title">Hide tickers tagged as:</div>
                  {allTagSets.map((ts) => (
                    <label key={ts.id} className="tag-filter-item">
                      <input
                        type="checkbox"
                        checked={hiddenTags.includes(ts.id)}
                        onChange={() => toggleTag(ts.id)}
                      />
                      <span className="tag-filter-swatch" style={{ backgroundColor: ts.color }} />
                      <span>{ts.name}</span>
                      <span className="tag-filter-ticker-count">{ts.tickers.length}</span>
                    </label>
                  ))}
                  {hiddenTags.length > 0 && (
                    <button className="tag-filter-clear" onClick={() => setHiddenTags([])}>
                      Clear filters
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {error && <div className="trending-error">Failed to load: {error}</div>}

      {showSkeleton && <SkeletonCards />}

      {isEmpty && (
        <div className="empty-state">
          <FiBarChart className="empty-icon" />
          <p>No trending tickers found for this window.</p>
          <p className="empty-hint">Try selecting a wider time window.</p>
        </div>
      )}

      {!showSkeleton && !isEmpty && viewMode === 'cards' && (
        <div className="ticker-grid">
          {sortedTickers.map((t) => (
            <TickerCard
              key={t.ticker}
              ticker={t}
              onClick={() => navigate(`/tickers/${t.ticker}`)}
            />
          ))}
        </div>
      )}

      {!showSkeleton && !isEmpty && viewMode === 'table' && (
        <div className="ticker-table-wrap">
          <table className="ticker-table">
            <thead>
              <tr>
                <th>#</th>
                <th className="sortable" onClick={() => handleSort('ticker')}>
                  Ticker{sortIndicator('ticker')}
                </th>
                <th className="sortable" onClick={() => handleSort('mention_count')}>
                  Mentions{sortIndicator('mention_count')}
                </th>
                <th>Tags</th>
                <th>Sparkline</th>
                <th>Trend</th>
                <th>Sentiment</th>
              </tr>
            </thead>
            <tbody>
              {sortedTickers.map((t, i) => (
                <tr key={t.ticker} onClick={() => navigate(`/tickers/${t.ticker}`)} className="clickable-row">
                  <td className="rank-cell">{i + 1}</td>
                  <td className="ticker-cell">{t.ticker}</td>
                  <td>{t.mention_count.toLocaleString()}</td>
                  <td>
                    <div className="table-tags">
                      {t.tags?.map((tag) => (
                        <span key={tag.id} className="tag-chip" style={{ backgroundColor: tag.color }}>
                          {tag.name}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <SparklineChart data={t.sparkline} id={`tbl-${t.ticker}`} />
                  </td>
                  <td><TrendIcon trend={t.trend} /></td>
                  <td><SentimentBadge sentiment={t.sentiment} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
