import { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { AreaChart, Area, ResponsiveContainer } from 'recharts'
import { FiGrid, FiList, FiRefreshCw, FiTrendingUp, FiTrendingDown, FiMinus, FiBarChart, FiArrowRight, FiFilter } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import './TrendingDashboard.css'

const WINDOWS = ['1h', '6h', '24h', '7d']
const LIMIT_OPTIONS = [20, 50, 100]
const COUNT_MODES = [
  { value: 'mentions', label: 'Mentions' },
  { value: 'authors', label: 'Authors' },
  { value: 'posts', label: 'Posts' },
]
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

function formatPrice(v) {
  if (v == null) return '-'
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatLargeNum(n) {
  if (n == null) return '-'
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`
  return `$${n.toLocaleString()}`
}

function formatPct(v) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function PctCell({ value }) {
  if (value == null) return <span className="text-muted">-</span>
  const cls = value >= 0 ? 'pct-positive' : 'pct-negative'
  return <span className={cls}>{formatPct(value)}</span>
}

function TickerCard({ ticker, countModeLabel, onClick }) {
  const f = ticker.fundamentals
  return (
    <div className="ticker-card" onClick={onClick} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onClick()}>
      <div className="ticker-card-header">
        <span className="ticker-symbol">{ticker.ticker}</span>
        <TrendIcon trend={ticker.trend} />
      </div>
      {f?.name && <div className="ticker-card-name">{f.name}</div>}
      <SentimentBadge sentiment={ticker.sentiment} />
      <div className="ticker-card-mentions">{ticker.count.toLocaleString()} {countModeLabel}</div>
      {f && (
        <div className="ticker-card-fundamentals">
          {f.current_price != null && (
            <span className="ticker-card-price">{formatPrice(f.current_price)}</span>
          )}
          {f.pct_change_prev != null && (
            <PctCell value={f.pct_change_prev} />
          )}
          {f.market_cap != null && (
            <span className="ticker-card-mcap">{formatLargeNum(f.market_cap)}</span>
          )}
        </div>
      )}
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
  const [limit, setLimit] = usePersistedState('trending-limit', 20)
  const [countMode, setCountMode] = usePersistedState('count-mode', 'mentions')
  const [viewMode, setViewMode] = usePersistedState('trending-view', 'cards')
  const [autoRefresh, setAutoRefresh] = usePersistedState('trending-autorefresh', false)
  const [countdown, setCountdown] = useState(REFRESH_INTERVAL)
  const [sortKey, setSortKey] = useState('count')
  const [sortDir, setSortDir] = useState('desc')
  const [refreshing, setRefreshing] = useState(false)
  const [hiddenTags, setHiddenTags] = usePersistedState('trending-hidden-tags', [])
  const [allTagSets, setAllTagSets] = useState([])
  const [filterOpen, setFilterOpen] = useState(false)
  const filterRef = useRef(null)
  const intervalRef = useRef(null)

  const countModeLabel = COUNT_MODES.find((m) => m.value === countMode)?.label.toLowerCase() || 'mentions'

  const fetchData = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)
    try {
      const res = await get(`/tickers/trending?window=${window}&limit=${limit}&count_mode=${countMode}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [window, limit, countMode])

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
        let aVal, bVal
        // Fundamentals fields are nested under .fundamentals
        const fundKeys = ['current_price', 'pct_change_prev', 'pct_change_open', 'market_cap', 'volume']
        if (fundKeys.includes(sortKey)) {
          aVal = a.fundamentals?.[sortKey]
          bVal = b.fundamentals?.[sortKey]
        } else {
          aVal = a[sortKey]
          bVal = b[sortKey]
        }
        if (sortKey === 'ticker') {
          aVal = (aVal || '').toLowerCase()
          bVal = (bVal || '').toLowerCase()
          return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
        }
        // Treat null/undefined as -Infinity for numeric sorts
        if (aVal == null) aVal = -Infinity
        if (bVal == null) bVal = -Infinity
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
          <div className="limit-selector">
            {LIMIT_OPTIONS.map((n) => (
              <button
                key={n}
                className={`window-btn ${limit === n ? 'active' : ''}`}
                onClick={() => setLimit(n)}
              >
                {n}
              </button>
            ))}
          </div>
          <div className="count-mode-selector">
            {COUNT_MODES.map((m) => (
              <button
                key={m.value}
                className={`window-btn ${countMode === m.value ? 'active' : ''}`}
                onClick={() => setCountMode(m.value)}
              >
                {m.label}
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
              countModeLabel={countModeLabel}
              onClick={() => navigate(`/tickers/${t.ticker}`)}
            />
          ))}
        </div>
      )}

      {!showSkeleton && !isEmpty && viewMode === 'table' && (
        <div className="ticker-table-wrap">
          <table className="ticker-table">
            <colgroup>
              <col style={{ width: '36px' }} />
              <col style={{ width: '14%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '110px' }} />
              <col style={{ width: '50px' }} />
              <col style={{ width: '90px' }} />
            </colgroup>
            <thead>
              <tr>
                <th>#</th>
                <th className="sortable" onClick={() => handleSort('ticker')}>
                  Ticker{sortIndicator('ticker')}
                </th>
                <th className="sortable num-col" onClick={() => handleSort('current_price')}>
                  Price{sortIndicator('current_price')}
                </th>
                <th className="sortable num-col" onClick={() => handleSort('pct_change_prev')}>
                  Chg%{sortIndicator('pct_change_prev')}
                </th>
                <th className="sortable num-col" onClick={() => handleSort('market_cap')}>
                  Mkt Cap{sortIndicator('market_cap')}
                </th>
                <th className="sortable num-col" onClick={() => handleSort('count')}>
                  {COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions'}{sortIndicator('count')}
                </th>
                <th>Tags</th>
                <th>Sparkline</th>
                <th>Trend</th>
                <th>Sentiment</th>
              </tr>
            </thead>
            <tbody>
              {sortedTickers.map((t, i) => {
                const f = t.fundamentals
                return (
                  <tr key={t.ticker} onClick={() => navigate(`/tickers/${t.ticker}`)} className="clickable-row">
                    <td className="rank-cell">{i + 1}</td>
                    <td className="ticker-cell">
                      {t.ticker}
                      {f?.name && <span className="table-ticker-name">{f.name}</span>}
                    </td>
                    <td className="num-col">{f?.current_price != null ? formatPrice(f.current_price) : '-'}</td>
                    <td className="num-col"><PctCell value={f?.pct_change_prev} /></td>
                    <td className="num-col">{f?.market_cap != null ? formatLargeNum(f.market_cap) : '-'}</td>
                    <td className="num-col">{t.count.toLocaleString()}</td>
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
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
