import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AreaChart, Area, ResponsiveContainer,
} from 'recharts'
import { FiChevronLeft, FiChevronRight, FiTrendingUp, FiTrendingDown, FiMinus, FiArrowRight, FiAlertTriangle } from 'react-icons/fi'
import { get } from '../api/client'
import './Historical.css'

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

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

function HealthBar({ days, selectedDate, onSelectDate }) {
  if (!days || days.length === 0) return null
  const maxCount = Math.max(...days.map((d) => d.mention_count), 1)

  return (
    <div className="health-bar-wrap">
      <div className="health-bar-title">Collection Health — Last {days.length} Days</div>
      <div className="health-bar">
        {days.map((d) => {
          const height = Math.max(2, (d.mention_count / maxCount) * 40)
          const isSelected = d.date === selectedDate
          return (
            <div
              key={d.date}
              className={`health-bar-day ${d.status} ${isSelected ? 'selected' : ''}`}
              title={`${d.date}: ${d.mention_count.toLocaleString()} mentions (${d.status})`}
              onClick={() => onSelectDate(d.date)}
            >
              <div className="health-bar-col" style={{ height: `${height}px` }} />
            </div>
          )
        })}
      </div>
      <div className="health-bar-legend">
        <span className="health-legend-item healthy">Healthy</span>
        <span className="health-legend-item low">Low</span>
        <span className="health-legend-item gap"><FiAlertTriangle /> Gap</span>
      </div>
    </div>
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
                title={cell.health ? `${cell.mention_count || cell.health.mention_count} mentions` : ''}
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
  const [healthDays, setHealthDays] = useState(null)
  const [healthMap, setHealthMap] = useState(null)
  const [trending, setTrending] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [earliestDate, setEarliestDate] = useState(null)
  const [latestDate, setLatestDate] = useState(null)

  const fetchHealth = useCallback(async () => {
    try {
      const res = await get('/system/collection-health?days=90')
      setHealthDays(res.days)
      setEarliestDate(res.earliest_date)
      setLatestDate(res.latest_date)
      const map = new Map()
      for (const d of res.days) {
        map.set(d.date, d)
      }
      setHealthMap(map)
    } catch {
      setHealthDays([])
    }
  }, [])

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
    fetchTrending(selectedDate)
  }, [selectedDate, fetchTrending])

  const handleSelectDate = (date) => {
    setSelectedDate(date)
  }

  const goToTicker = (ticker, date) => {
    navigate(`/tickers/${ticker}?date=${date}`)
  }

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

      <div className="historical-layout">
        <div className="historical-left-panel">
          <Calendar
            selectedDate={selectedDate}
            healthMap={healthMap}
            onSelectDate={handleSelectDate}
            earliestDate={earliestDate}
            latestDate={latestDate}
          />
          {healthDays && (
            <HealthBar
              days={healthDays}
              selectedDate={selectedDate}
              onSelectDate={handleSelectDate}
            />
          )}
        </div>

        <div className="historical-right-panel">
          {loading && <div className="historical-loading">Loading trending tickers for {selectedDate}...</div>}
          {error && <div className="historical-error">Failed to load: {error}</div>}
          {!loading && !error && trending?.tickers?.length === 0 && (
            <div className="historical-empty">
              <FiAlertTriangle className="historical-empty-icon" />
              <p>No data collected on {selectedDate}.</p>
              <p className="historical-empty-hint">This may be a collection gap — the scraper may not have been running.</p>
            </div>
          )}
          {!loading && !error && trending?.tickers?.length > 0 && (
            <>
              <div className="historical-trending-title">
                Trending Tickers — {trending.tickers.length} found
              </div>
              <div className="ticker-table-wrap">
                <table className="ticker-table">
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
                    {trending.tickers.map((t, i) => {
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
            </>
          )}
        </div>
      </div>
    </div>
  )
}
