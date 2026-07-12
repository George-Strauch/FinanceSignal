import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  AreaChart, Area, LineChart, Line,
} from 'recharts'
import { FiChevronLeft, FiChevronRight, FiAlertTriangle } from 'react-icons/fi'
import { get } from '../api/client'
import TagFilterButton from '../components/TagFilterButton'
import usePersistedState from '../hooks/usePersistedState'
import './Historical.css'

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const GRANULARITIES = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
]

const SORT_OPTIONS = [
  { value: 'mentions', label: 'Mentions' },
  { value: 'price_pct', label: 'Price %' },
  { value: 'market_cap', label: 'Market Cap' },
]

function todayET() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
}

function parseDateStr(s) {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function dateStr(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Floor a date to the start of its granularity bucket */
function floorToBucket(ds, granularity) {
  const d = parseDateStr(ds)
  if (granularity === 'week') {
    const monday = new Date(d)
    monday.setDate(d.getDate() - d.getDay())
    return dateStr(monday)
  }
  if (granularity === 'month') {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`
  }
  return ds
}

/** Format a timestamp string for human-readable display */
function formatTsHuman(ts) {
  if (!ts) return '-'
  // ts could be "2026-03-19T00:00:00" or "2026-03-19T09:30:00-04:00" or "2026-03-19" or "2026-03"
  if (ts.length === 7) {
    // "2026-03" — month bucket
    const [y, m] = ts.split('-')
    return `${MONTH_NAMES[parseInt(m) - 1]} ${y}`
  }
  if (ts.length === 10) {
    // "2026-03-19" — day or week bucket
    const d = parseDateStr(ts)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  }
  // Full ISO — parse and format
  try {
    const d = new Date(ts)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
      ' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' }) + ' UTC'
  } catch {
    return ts
  }
}

function MentionSparkline({ data, id }) {
  if (!data || data.length === 0) return <span className="hist-no-data">-</span>
  const gradientId = `hist-spark-${id}`
  return (
    <ResponsiveContainer width={100} height={32}>
      <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--accent))" stopOpacity={0.4} />
            <stop offset="100%" stopColor="rgb(var(--accent))" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke="rgb(var(--accent))" strokeWidth={1.5} fill={`url(#${gradientId})`} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function PriceSparkline({ data }) {
  if (!data || data.length === 0) return <span className="hist-no-data">-</span>
  return (
    <ResponsiveContainer width={100} height={32}>
      <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <Line type="monotone" dataKey="p" stroke="rgb(99, 102, 241)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}

function formatLargeNum(n) {
  if (n == null) return '-'
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`
  return `$${n.toLocaleString()}`
}

function HistogramChart({ bins, selectedDate, onBarClick, granularity }) {
  if (!bins || bins.length === 0) {
    return <div className="histogram-empty">No collection data available.</div>
  }

  const data = bins.map((b) => ({
    ...b,
    isSelected: b.date === selectedDate,
  }))

  const handleClick = (data) => {
    if (data && data.date) {
      onBarClick(data.date)
    }
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 10, left: 0, bottom: 20 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(var(--soft-border), 0.2)" />
        <XAxis
          dataKey="label"
          tick={{ fill: 'rgb(var(--soft-text))', fontSize: 10 }}
          stroke="rgba(var(--soft-border), 0.4)"
          minTickGap={20}
          angle={-30}
          textAnchor="end"
          height={50}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fill: 'rgb(var(--soft-text))', fontSize: 11 }}
          stroke="rgba(var(--soft-border), 0.4)"
        />
        <Tooltip
          contentStyle={{
            background: 'rgb(var(--primary-color))',
            border: '1px solid rgba(var(--soft-border), var(--soft-border-alpha))',
            borderRadius: 8,
            fontSize: '0.82rem',
          }}
          formatter={(value) => [value.toLocaleString(), 'Posts Collected']}
        />
        <Bar
          dataKey="count"
          name="Posts"
          fill="rgba(var(--accent), 0.5)"
          stroke="rgba(var(--accent), 0.8)"
          isAnimationActive={false}
          cursor="pointer"
          onClick={handleClick}
        />
      </BarChart>
    </ResponsiveContainer>
  )
}

function Calendar({ selectedDate, healthMap, onSelectDate, earliestDate, latestDate, granularity }) {
  const [viewMonth, setViewMonth] = useState(() => {
    const d = parseDateStr(selectedDate)
    return new Date(d.getFullYear(), d.getMonth(), 1)
  })
  const [lastSelected, setLastSelected] = useState(selectedDate)

  if (selectedDate !== lastSelected) {
    setLastSelected(selectedDate)
    const d = parseDateStr(selectedDate)
    const newMonth = new Date(d.getFullYear(), d.getMonth(), 1)
    if (newMonth.getFullYear() !== viewMonth.getFullYear() || newMonth.getMonth() !== viewMonth.getMonth()) {
      setViewMonth(newMonth)
    }
  }

  const year = viewMonth.getFullYear()
  const month = viewMonth.getMonth()
  const firstDay = new Date(year, month, 1)
  const lastDay = new Date(year, month + 1, 0)
  const startDow = firstDay.getDay()
  const daysInMonth = lastDay.getDate()

  const earliest = earliestDate ? parseDateStr(earliestDate) : null
  const latest = latestDate ? parseDateStr(latestDate) : null

  // Compute which dates are in the same bucket as selectedDate
  const selectedBucketStart = floorToBucket(selectedDate, granularity)

  const cells = []
  for (let i = 0; i < startDow; i++) {
    cells.push(null)
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(year, month, d)
    const ds = dateStr(date)
    const isDisabled = (earliest && date < earliest) || (latest && date > latest)
    const health = healthMap?.get(ds)
    const isInBucket = granularity !== 'day' && floorToBucket(ds, granularity) === selectedBucketStart
    cells.push({ date: ds, day: d, isDisabled, health, isInBucket })
  }

  const prevMonth = () => setViewMonth(new Date(year, month - 1, 1))
  const nextMonth = () => setViewMonth(new Date(year, month + 1, 1))

  return (
    <div className="calendar-wrap">
      <div className="calendar-header">
        <button className="calendar-nav-btn" onClick={prevMonth}><FiChevronLeft /></button>
        <span className="calendar-month-label">{MONTH_NAMES[month]} {year}</span>
        <button className="calendar-nav-btn" onClick={nextMonth}><FiChevronRight /></button>
      </div>
      <div className="calendar-grid">
        {DOW.map((d) => (
          <div key={d} className="calendar-dow">{d}</div>
        ))}
        {cells.map((cell, i) => (
          <div key={i} className="calendar-cell-wrap">
            {cell && !cell.isDisabled && (
              <div
                className={`calendar-day ${cell.date === selectedDate ? 'selected' : ''} ${cell.isInBucket ? 'in-bucket' : ''} ${cell.health?.status || ''}`}
                onClick={() => onSelectDate(cell.date)}
                title={cell.health ? `${cell.health.mention_count} mentions` : ''}
              >
                {cell.day}
                {cell.health?.status === 'gap' && <span className="calendar-gap-dot" />}
                {cell.health?.status === 'low' && <span className="calendar-low-dot" />}
              </div>
            )}
            {cell && cell.isDisabled && (
              <div className="calendar-day disabled">{cell.day}</div>
            )}
            {!cell && <div className="calendar-day empty" />}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Historical() {
  const navigate = useNavigate()
  const [selectedDate, setSelectedDate] = useState(todayET)
  const [healthMap, setHealthMap] = useState(null)
  const [trending, setTrending] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [earliestDate, setEarliestDate] = useState(null)
  const [latestDate, setLatestDate] = useState(null)
  const [histogramBins, setHistogramBins] = useState(null)
  const [histogramStart, setHistogramStart] = usePersistedState('hist-histogram-start', '2026-01-01')

  const [allTagSets, setAllTagSets] = useState([])
  const [hiddenTags, setHiddenTags] = useState([])
  const [tagsInitialized, setTagsInitialized] = useState(false)

  const [forwardDays, setForwardDays] = usePersistedState('hist-forward-days', 7)
  const [lookbackDays, setLookbackDays] = usePersistedState('hist-lookback-days', 0)
  const [granularity, setGranularity] = usePersistedState('hist-granularity', 'day')
  const [sortBy, setSortBy] = usePersistedState('hist-sort-by', 'mentions')

  const fetchHealth = useCallback(async () => {
    try {
      const res = await get('/system/collection-health?days=90')
      setEarliestDate(res.earliest_date)
      setLatestDate(res.latest_date)
      const map = new Map()
      for (const d of res.days) {
        map.set(d.date, d)
      }
      setHealthMap(map)
    } catch {
      setHealthMap(new Map())
    }
  }, [])

  const fetchHistogram = useCallback(async () => {
    try {
      const params = new URLSearchParams({ granularity })
      if (histogramStart) params.set('start', histogramStart)
      const res = await get(`/mentions/histogram?${params}`)
      setHistogramBins(res.bins)
    } catch {
      setHistogramBins([])
    }
  }, [granularity, histogramStart])

  const fetchTrending = useCallback(async (date) => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        date,
        limit: '50',
        forward_days: String(forwardDays),
        lookback_days: String(lookbackDays),
        granularity,
      })
      const res = await get(`/tickers/historical?${params}`)
      setTrending(res)
    } catch (err) {
      setError(err.message)
      setTrending(null)
    } finally {
      setLoading(false)
    }
  }, [forwardDays, lookbackDays, granularity])

  useEffect(() => {
    fetchHealth()
  }, [fetchHealth])

  useEffect(() => {
    fetchHistogram()
  }, [fetchHistogram])

  useEffect(() => {
    fetchTrending(selectedDate)
  }, [selectedDate, fetchTrending])

  useEffect(() => {
    get('/ticker-tags').then((res) => {
      setAllTagSets(res.tag_sets)
      if (!tagsInitialized) {
        const defaultHidden = res.tag_sets
          .filter((ts) => ['ambiguous', 'crypto', 'etf'].includes(ts.id))
          .map((ts) => ts.id)
        setHiddenTags(defaultHidden)
        setTagsInitialized(true)
      }
    }).catch(() => {})
  }, [tagsInitialized])

  const handleSelectDate = (date) => {
    setSelectedDate(date)
  }

  const handleHistogramClick = (date) => {
    setSelectedDate(date)
  }

  const goToTicker = (ticker, date) => {
    navigate(`/tickers/${ticker}?date=${date}`)
  }

  const toggleTag = (tagId) => {
    setHiddenTags((prev) =>
      prev.includes(tagId) ? prev.filter((id) => id !== tagId) : [...prev, tagId]
    )
  }

  const filteredTrending = trending?.tickers
    ? trending.tickers.filter((t) => {
        if (hiddenTags.length === 0) return true
        return !t.tags?.some((tag) => hiddenTags.includes(tag.id))
      })
    : []

  // Sort filtered trending by selected sort option
  const sortedTrending = [...filteredTrending].sort((a, b) => {
    if (sortBy === 'price_pct') {
      const av = a.price_change_pct ?? -Infinity
      const bv = b.price_change_pct ?? -Infinity
      return bv - av
    }
    if (sortBy === 'market_cap') {
      const av = a.fundamentals?.market_cap ?? -Infinity
      const bv = b.fundamentals?.market_cap ?? -Infinity
      return bv - av
    }
    // mentions (default — preserves original ranking by count)
    return b.count - a.count
  })

  // Sparkline bounds text (human-readable)
  const mentionBounds = (() => {
    if (!sortedTrending || sortedTrending.length === 0) return null
    const allPoints = sortedTrending.flatMap((t) => t.sparkline || [])
    if (allPoints.length === 0) return null
    const times = allPoints.map((p) => p.t).sort()
    return { start: times[0], end: times[times.length - 1] }
  })()

  const priceBounds = (() => {
    if (!sortedTrending || sortedTrending.length === 0) return null
    const allPoints = sortedTrending.flatMap((t) => t.price_sparkline || [])
    if (allPoints.length === 0) return null
    const times = allPoints.map((p) => p.t).sort()
    return { start: times[0], end: times[times.length - 1] }
  })()

  const hasPriceData = sortedTrending.some((t) => t.price_sparkline && t.price_sparkline.length > 0)
  const hasPriceChange = sortedTrending.some((t) => t.price_change_pct != null)
  const hasMarketCap = sortedTrending.some((t) => t.fundamentals?.market_cap != null)

  // Bucket label for the selected date
  const bucketLabel = (() => {
    const d = parseDateStr(selectedDate)
    if (granularity === 'week') {
      const start = floorToBucket(selectedDate, 'week')
      const startDate = parseDateStr(start)
      const endDate = new Date(startDate)
      endDate.setDate(startDate.getDate() + 6)
      return `${startDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} – ${endDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
    }
    if (granularity === 'month') {
      return `${MONTH_NAMES[d.getMonth()]} ${d.getFullYear()}`
    }
    return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
  })()

  return (
    <div className="historical-page">
      <div className="historical-header">
        <h1>Historical</h1>
        <div className="historical-header-controls">
          <div className="histogram-control-group">
            <label className="histogram-control-label">Granularity</label>
            <select
              className="histogram-granularity-select"
              value={granularity}
              onChange={(e) => setGranularity(e.target.value)}
            >
              {GRANULARITIES.map((g) => (
                <option key={g.value} value={g.value}>{g.label}</option>
              ))}
            </select>
          </div>
          <div className="historical-selected-date">{bucketLabel}</div>
        </div>
      </div>

      <div className="historical-top-section">
        <div className="historical-calendar-panel">
          <Calendar
            selectedDate={selectedDate}
            healthMap={healthMap}
            onSelectDate={handleSelectDate}
            earliestDate={earliestDate}
            latestDate={latestDate}
            granularity={granularity}
          />
        </div>
        <div className="historical-histogram-panel">
          <div className="histogram-controls">
            <div className="histogram-control-group">
              <label className="histogram-control-label">Start</label>
              <input
                type="date"
                className="histogram-date-input"
                value={histogramStart}
                onChange={(e) => setHistogramStart(e.target.value)}
              />
            </div>
          </div>
          <div className="histogram-chart-wrap">
            <HistogramChart
              bins={histogramBins}
              selectedDate={selectedDate}
              onBarClick={handleHistogramClick}
              granularity={granularity}
            />
          </div>
        </div>
      </div>

      <div className="historical-bottom-section">
        <div className="historical-config-panel">
          <div className="historical-config-row">
            <div className="historical-config-group">
              <label className="histogram-control-label">Days Forward (Price)</label>
              <input
                type="number"
                className="histogram-date-input"
                value={forwardDays}
                min={0}
                max={365}
                onChange={(e) => setForwardDays(Math.max(0, Math.min(365, Number(e.target.value) || 0)))}
              />
            </div>
            <div className="historical-config-group">
              <label className="histogram-control-label">Days Back (Mentions)</label>
              <input
                type="number"
                className="histogram-date-input"
                value={lookbackDays}
                min={0}
                max={365}
                onChange={(e) => setLookbackDays(Math.max(0, Math.min(365, Number(e.target.value) || 0)))}
              />
            </div>
            <div className="historical-config-group">
              <label className="histogram-control-label">Sort By</label>
              <select
                className="histogram-granularity-select"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
              >
                {SORT_OPTIONS.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <div className="historical-config-info">
              {mentionBounds && (
                <div className="hist-bounds-text">
                  <span className="hist-bounds-label">Mentions:</span>
                  <span className="hist-bounds-value">{formatTsHuman(mentionBounds.start)}</span>
                  <span className="hist-bounds-sep">→</span>
                  <span className="hist-bounds-value">{formatTsHuman(mentionBounds.end)}</span>
                </div>
              )}
              {priceBounds && (
                <div className="hist-bounds-text">
                  <span className="hist-bounds-label">Price:</span>
                  <span className="hist-bounds-value">{formatTsHuman(priceBounds.start)}</span>
                  <span className="hist-bounds-sep">→</span>
                  <span className="hist-bounds-value">{formatTsHuman(priceBounds.end)}</span>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="historical-table-header">
          <div className="historical-trending-title">
            Trending Tickers — {bucketLabel}
            {sortedTrending.length > 0 && <span className="hist-trending-count">({sortedTrending.length})</span>}
          </div>
          <TagFilterButton
            tagSets={allTagSets}
            hiddenTagIds={hiddenTags}
            onToggleTag={toggleTag}
            onClearTags={() => setHiddenTags([])}
          />
        </div>

        {loading && <div className="historical-loading">Loading trending tickers for {bucketLabel}...</div>}
        {error && <div className="historical-error">Failed to load: {error}</div>}
        {!loading && !error && sortedTrending.length === 0 && (
          <div className="historical-empty">
            <FiAlertTriangle className="historical-empty-icon" />
            <p>No data collected for {bucketLabel}.</p>
            <p className="historical-empty-hint">This may be a collection gap — the scraper may not have been running.</p>
          </div>
        )}
        {!loading && !error && sortedTrending.length > 0 && (
          <div className="ticker-table-wrap">
            <table className="historical-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Ticker</th>
                  <th>Mentions</th>
                  <th>Subreddits</th>
                  <th>Mentions Sparkline</th>
                  {hasPriceData && <th>Price ({forwardDays}d fwd)</th>}
                  {hasPriceChange && <th>Price %</th>}
                  {hasMarketCap && <th>Mkt Cap</th>}
                </tr>
              </thead>
              <tbody>
                {sortedTrending.map((t, i) => {
                  const f = t.fundamentals
                  const pct = t.price_change_pct
                  return (
                    <tr
                      key={t.ticker}
                      onClick={() => goToTicker(t.ticker, selectedDate)}
                      className="clickable-row"
                    >
                      <td className="rank-cell">{i + 1}</td>
                      <td className="ticker-cell">
                        {t.ticker}
                        {f?.name && <span className="table-ticker-name">{f.name}</span>}
                      </td>
                      <td className="num-col">{t.count.toLocaleString()}</td>
                      <td className="hist-subs">
                        {t.subreddits?.slice(0, 3).map((s) => (
                          <span key={s} className="hist-sub-badge">{s}</span>
                        ))}
                        {t.subreddits?.length > 3 && (
                          <span className="hist-sub-more">+{t.subreddits.length - 3}</span>
                        )}
                      </td>
                      <td><MentionSparkline data={t.sparkline} id={t.ticker} /></td>
                      {hasPriceData && (
                        <td><PriceSparkline data={t.price_sparkline} /></td>
                      )}
                      {hasPriceChange && (
                        <td className={`num-col ${pct != null ? (pct >= 0 ? 'hist-pct-pos' : 'hist-pct-neg') : ''}`}>
                          {pct != null ? `${pct >= 0 ? '+' : ''}${pct}%` : '-'}
                        </td>
                      )}
                      {hasMarketCap && (
                        <td className="num-col">{f?.market_cap != null ? formatLargeNum(f.market_cap) : '-'}</td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
