import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  AreaChart, Area,
} from 'recharts'
import { FiChevronLeft, FiChevronRight, FiTrendingUp, FiTrendingDown, FiMinus, FiArrowRight, FiAlertTriangle } from 'react-icons/fi'
import { get } from '../api/client'
import TagFilterButton from '../components/TagFilterButton'
import './Historical.css'

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const GRANULARITIES = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
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

function SparklineChart({ data, id }) {
  if (!data || data.length === 0) return null
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

function TrendIcon({ trend }) {
  if (trend === 'up') return <FiTrendingUp className="trend-indicator up" />
  if (trend === 'down') return <FiTrendingDown className="trend-indicator down" />
  return <FiMinus className="trend-indicator flat" />
}

function SentimentBadge({ sentiment }) {
  if (!sentiment) return null
  const { label } = sentiment
  const icon = label === 'bullish' ? <FiTrendingUp /> : label === 'bearish' ? <FiTrendingDown /> : <FiArrowRight />
  return <span className={`sentiment-badge sentiment-${label}`}>{icon} {label}</span>
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

function HistogramChart({ bins, selectedDate, onBarClick }) {
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

function Calendar({ selectedDate, healthMap, onSelectDate, earliestDate, latestDate }) {
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

  const cells = []
  for (let i = 0; i < startDow; i++) {
    cells.push(null)
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(year, month, d)
    const ds = dateStr(date)
    const isDisabled = (earliest && date < earliest) || (latest && date > latest)
    const health = healthMap?.get(ds)
    cells.push({ date: ds, day: d, isDisabled, health })
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
                className={`calendar-day ${cell.date === selectedDate ? 'selected' : ''} ${cell.health?.status || ''}`}
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
  const [histogramStart, setHistogramStart] = useState('')
  const [histogramGranularity, setHistogramGranularity] = useState('day')

  const [allTagSets, setAllTagSets] = useState([])
  const [hiddenTags, setHiddenTags] = useState([])

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
      const params = new URLSearchParams({ granularity: histogramGranularity })
      if (histogramStart) params.set('start', histogramStart)
      const res = await get(`/mentions/histogram?${params}`)
      setHistogramBins(res.bins)
      if (!histogramStart && res.start) {
        setHistogramStart(res.start)
      }
    } catch {
      setHistogramBins([])
    }
  }, [histogramGranularity, histogramStart])

  const fetchTrending = useCallback(async (date) => {
    setLoading(true)
    setError(null)
    try {
      const res = await get(`/tickers/historical?date=${date}&limit=50`)
      setTrending(res)
    } catch (err) {
      setError(err.message)
      setTrending(null)
    } finally {
      setLoading(false)
    }
  }, [])

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
    get('/ticker-tags').then((res) => setAllTagSets(res.tag_sets)).catch(() => {})
  }, [])

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

  return (
    <div className="historical-page">
      <div className="historical-header">
        <h1>Historical</h1>
        <div className="historical-selected-date">
          {parseDateStr(selectedDate).toLocaleDateString('en-US', {
            weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
          })}
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
            <div className="histogram-control-group">
              <label className="histogram-control-label">Granularity</label>
              <select
                className="histogram-granularity-select"
                value={histogramGranularity}
                onChange={(e) => setHistogramGranularity(e.target.value)}
              >
                {GRANULARITIES.map((g) => (
                  <option key={g.value} value={g.value}>{g.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="histogram-chart-wrap">
            <HistogramChart
              bins={histogramBins}
              selectedDate={selectedDate}
              onBarClick={handleHistogramClick}
            />
          </div>
        </div>
      </div>

      <div className="historical-bottom-section">
        <div className="historical-table-header">
          <div className="historical-trending-title">
            Trending Tickers — {selectedDate}
            {filteredTrending.length > 0 && <span className="hist-trending-count">({filteredTrending.length})</span>}
          </div>
          <TagFilterButton
            tagSets={allTagSets}
            hiddenTagIds={hiddenTags}
            onToggleTag={toggleTag}
            onClearTags={() => setHiddenTags([])}
          />
        </div>

        {loading && <div className="historical-loading">Loading trending tickers for {selectedDate}...</div>}
        {error && <div className="historical-error">Failed to load: {error}</div>}
        {!loading && !error && filteredTrending.length === 0 && (
          <div className="historical-empty">
            <FiAlertTriangle className="historical-empty-icon" />
            <p>No data collected on {selectedDate}.</p>
            <p className="historical-empty-hint">This may be a collection gap — the scraper may not have been running.</p>
          </div>
        )}
        {!loading && !error && filteredTrending.length > 0 && (
          <div className="ticker-table-wrap">
            <table className="historical-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Ticker</th>
                  <th>Mentions</th>
                  <th>Subreddits</th>
                  <th>Sparkline</th>
                  <th>Trend</th>
                  <th>Sentiment</th>
                </tr>
              </thead>
              <tbody>
                {filteredTrending.map((t, i) => {
                  const f = t.fundamentals
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
                        {f?.current_price != null && (
                          <span className="hist-price">{formatPrice(f.current_price)}</span>
                        )}
                        {f?.market_cap != null && (
                          <span className="hist-mcap">{formatLargeNum(f.market_cap)}</span>
                        )}
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
                      <td><SparklineChart data={t.sparkline} id={t.ticker} /></td>
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
    </div>
  )
}