import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ComposedChart, Line, Bar,
} from 'recharts'
import { FiArrowLeft, FiExternalLink, FiTrendingUp, FiTrendingDown, FiArrowRight, FiPlus } from 'react-icons/fi'
import { get, post, del } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import PostFeed from '../components/PostFeed'
import { recordTickerVisit } from './Tickers'
import './TickerDetail.css'

const WINDOWS = ['1h', '6h', '24h', '7d', '30d']
const PRICE_RANGES = ['1D', '5D', '1M', '3M', '6M', '1Y']
const RANGE_API_MAP = { '1D': '1d', '5D': '5d', '1M': '1mo', '3M': '3mo', '6M': '6mo', '1Y': '1y' }

const PALETTE = [
  '99, 102, 241',   // indigo
  '244, 114, 182',  // pink
  '52, 211, 153',   // emerald
  '251, 191, 36',   // amber
  '96, 165, 250',   // blue
  '167, 139, 250',  // violet
  '248, 113, 113',  // red
  '45, 212, 191',   // teal
  '253, 186, 116',  // orange
  '156, 163, 175',  // gray
]

function pivotChartData(mentionsOverTime, subreddits) {
  const map = new Map()
  for (const row of mentionsOverTime) {
    if (!map.has(row.timestamp)) {
      const entry = { timestamp: row.timestamp }
      for (const sub of subreddits) entry[sub] = 0
      map.set(row.timestamp, entry)
    }
    map.get(row.timestamp)[row.subreddit] = row.count
  }
  return Array.from(map.values())
}

function formatTimestamp(ts) {
  if (!ts) return ''
  if (ts.length <= 10) return ts          // "2025-01-15" → as-is
  // "2025-01-15T14:00:00" → "01-15 14:00"
  return ts.slice(5, 10) + ' ' + ts.slice(11, 16)
}

function formatPrice(v) {
  if (v == null) return '-'
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatVolume(v) {
  if (v == null) return '-'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return v.toLocaleString()
}

const MONTH_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

function formatPriceTs(ts, range) {
  if (!ts) return ''
  if (range === '1D' || range === '5D') {
    // ts = "2025-01-15T14:00:00" (ET) — parse directly from string
    const mon = MONTH_ABBR[parseInt(ts.slice(5, 7), 10) - 1]
    const day = parseInt(ts.slice(8, 10), 10)
    const h24 = parseInt(ts.slice(11, 13), 10)
    const ampm = h24 >= 12 ? 'PM' : 'AM'
    const h12 = h24 % 12 || 12
    return `${mon} ${day} ${h12}:00 ${ampm}`
  }
  // ts = "2025-01-15" (ET date)
  const mon = MONTH_ABBR[parseInt(ts.slice(5, 7), 10) - 1]
  const day = parseInt(ts.slice(8, 10), 10)
  return `${mon} ${day}`
}

/** Merge price + mention data — mention-centric: every mention bucket is visible,
 *  price fills in where market was open. Off-market/weekend mentions always show. */
function mergePriceMentions(prices, mentions, range) {
  if (!mentions || mentions.length === 0) return prices

  const priceMap = new Map()
  for (const p of prices) priceMap.set(p.t, p)

  if (range === '1D' || range === '5D') {
    // Hourly: both keys are "2025-01-15T14:00:00" — union all timestamps
    const mentionMap = new Map()
    for (const m of mentions) mentionMap.set(m.t, m.v)

    const allKeys = new Set([...priceMap.keys(), ...mentionMap.keys()])
    return [...allKeys].sort().map(t => {
      const price = priceMap.get(t)
      return {
        t,
        c: price ? price.c : null,
        o: price ? price.o : null,
        h: price ? price.h : null,
        l: price ? price.l : null,
        v: price ? price.v : null,
        mentions: mentionMap.get(t) || null,
      }
    })
  }

  // Daily: price keys "2025-01-15", mention keys "2025-01-15T14:00:00"
  // Aggregate mentions to daily, then union with price dates
  const dailyMentions = new Map()
  for (const m of mentions) {
    const day = m.t.slice(0, 10)
    dailyMentions.set(day, (dailyMentions.get(day) || 0) + m.v)
  }

  const allKeys = new Set([...priceMap.keys(), ...dailyMentions.keys()])
  return [...allKeys].sort().map(t => {
    const price = priceMap.get(t)
    return {
      t,
      c: price ? price.c : null,
      o: price ? price.o : null,
      h: price ? price.h : null,
      l: price ? price.l : null,
      v: price ? price.v : null,
      mentions: dailyMentions.get(t) || null,
    }
  })
}

export default function TickerDetail() {
  const { ticker } = useParams()
  const navigate = useNavigate()
  const [window, setWindow] = usePersistedState('ticker-detail-window', '7d')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Price chart state
  const [priceRange, setPriceRange] = usePersistedState('ticker-price-range', '1M')
  const [priceData, setPriceData] = useState(null)
  const [priceLoading, setPriceLoading] = useState(true)
  const [marketInfo, setMarketInfo] = useState(null)
  const [infoLoading, setInfoLoading] = useState(true)
  const [mentionOverlay, setMentionOverlay] = usePersistedState('ticker-mention-overlay', false)
  const [mentionData, setMentionData] = useState(null)
  const [mentionLoading, setMentionLoading] = useState(false)

  // Tag quick-add state
  const [allTagSets, setAllTagSets] = useState([])
  const [tagMenuOpen, setTagMenuOpen] = useState(false)
  const tagMenuRef = useRef(null)

  // Record visit for recent tickers
  useEffect(() => {
    if (ticker) recordTickerVisit(ticker)
  }, [ticker])

  // Fetch Reddit mention detail
  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await get(`/tickers/${ticker}?window=${window}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [ticker, window])

  // Fetch price chart
  const fetchPriceChart = useCallback(async () => {
    setPriceLoading(true)
    try {
      const rangeParam = RANGE_API_MAP[priceRange]
      const res = await get(`/market/${ticker}/chart?range=${rangeParam}`)
      setPriceData(res.prices)
    } catch {
      setPriceData(null)
    } finally {
      setPriceLoading(false)
    }
  }, [ticker, priceRange])

  // Fetch market info
  const fetchMarketInfo = useCallback(async () => {
    setInfoLoading(true)
    try {
      const res = await get(`/market/${ticker}/info`)
      setMarketInfo(res)
    } catch {
      setMarketInfo(null)
    } finally {
      setInfoLoading(false)
    }
  }, [ticker])

  // Fetch hourly mentions for overlay
  const fetchMentions = useCallback(async () => {
    setMentionLoading(true)
    try {
      const rangeParam = RANGE_API_MAP[priceRange]
      const res = await get(`/mentions/${ticker}/hourly?range=${rangeParam}`)
      setMentionData(res.mentions)
    } catch {
      setMentionData(null)
    } finally {
      setMentionLoading(false)
    }
  }, [ticker, priceRange])

  useEffect(() => { fetchDetail() }, [fetchDetail])
  useEffect(() => { fetchPriceChart() }, [fetchPriceChart])
  useEffect(() => { fetchMarketInfo() }, [fetchMarketInfo])
  useEffect(() => {
    if (mentionOverlay) fetchMentions()
  }, [mentionOverlay, fetchMentions])

  // Fetch all tag sets for quick-add menu
  useEffect(() => {
    get('/ticker-tags').then((res) => setAllTagSets(res.tag_sets)).catch(() => {})
  }, [])

  // Close tag menu on click outside
  useEffect(() => {
    const handleClick = (e) => {
      if (tagMenuRef.current && !tagMenuRef.current.contains(e.target)) setTagMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const tickerUpper = ticker?.toUpperCase()
  const currentTagIds = new Set(data?.tags?.map((t) => t.id) || [])

  const handleToggleTag = async (tagSet) => {
    const isTagged = currentTagIds.has(tagSet.id)
    try {
      if (isTagged) {
        await del(`/ticker-tags/${tagSet.id}/tickers/${tickerUpper}`)
      } else {
        await post(`/ticker-tags/${tagSet.id}/tickers`, { tickers: [tickerUpper] })
      }
      // Refresh both detail data (for tags) and all tag sets (for counts)
      fetchDetail()
      get('/ticker-tags').then((res) => setAllTagSets(res.tag_sets)).catch(() => {})
    } catch {}
  }

  const subreddits = data ? Object.keys(data.mentions_by_subreddit) : []
  const chartData = data ? pivotChartData(data.mentions_over_time, subreddits) : []
  const topSubreddit = subreddits.length > 0 ? subreddits[0] : '-'

  // Merged data for price chart with optional mention overlay
  const mergedPriceData = priceData
    ? (mentionOverlay && mentionData
        ? mergePriceMentions(priceData, mentionData, priceRange)
        : priceData)
    : []

  const priceChangePositive = marketInfo?.day_change != null && marketInfo.day_change >= 0
  const yahooUrl = `https://finance.yahoo.com/quote/${ticker?.toUpperCase()}`

  return (
    <div className="ticker-detail">
      <div className="td-header">
        <button className="td-back" onClick={() => navigate(-1)}>
          <FiArrowLeft />
        </button>
        <div className="td-title-group">
          <div className="td-title-row">
            <h1 className="td-ticker">{ticker?.toUpperCase()}</h1>
            {data?.tags?.map((tag) => (
              <span key={tag.id} className="tag-chip" style={{ backgroundColor: tag.color }}>
                {tag.name}
              </span>
            ))}
            {allTagSets.length > 0 && (
              <div className="td-tag-add-wrap" ref={tagMenuRef}>
                <button
                  className="td-tag-add-btn"
                  onClick={() => setTagMenuOpen((v) => !v)}
                  title="Add or remove tags"
                >
                  <FiPlus />
                </button>
                {tagMenuOpen && (
                  <div className="td-tag-menu">
                    {allTagSets.map((ts) => {
                      const active = currentTagIds.has(ts.id)
                      return (
                        <button
                          key={ts.id}
                          className={`td-tag-menu-item ${active ? 'active' : ''}`}
                          onClick={() => handleToggleTag(ts)}
                        >
                          <span className="td-tag-menu-swatch" style={{ backgroundColor: ts.color }} />
                          <span className="td-tag-menu-name">{ts.name}</span>
                          {active && <span className="td-tag-menu-check">&#10003;</span>}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
            {marketInfo?.name && (
              <span className="td-company-name">{marketInfo.name}</span>
            )}
            <a
              className="td-yahoo-link"
              href={yahooUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="View on Yahoo Finance"
            >
              <FiExternalLink /> Yahoo Finance
            </a>
          </div>
          {data && (
            <span className="td-mention-count">
              {data.total_mentions.toLocaleString()} mentions
            </span>
          )}
        </div>
        <div className="td-window-selector">
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
      </div>

      {error && <div className="td-error">Failed to load: {error}</div>}

      {/* Market Info Cards */}
      {(marketInfo || infoLoading) && (
        <div className="td-market-info">
          {infoLoading && !marketInfo ? (
            <>
              {[...Array(6)].map((_, i) => (
                <div key={i} className="td-stat-card">
                  <div className="skel-line" style={{ height: 28, width: '60%', margin: '0 auto 6px' }} />
                  <div className="skel-line" style={{ height: 12, width: '50%', margin: '0 auto' }} />
                </div>
              ))}
            </>
          ) : marketInfo && (
            <>
              <div className="td-stat-card">
                <div className="td-stat-value">
                  {formatPrice(marketInfo.current_price)}
                </div>
                {marketInfo.day_change != null && (
                  <div className={`td-price-change ${priceChangePositive ? 'positive' : 'negative'}`}>
                    {priceChangePositive ? '+' : ''}{marketInfo.day_change} ({priceChangePositive ? '+' : ''}{marketInfo.day_change_pct}%)
                  </div>
                )}
                <div className="td-stat-label">Current Price</div>
              </div>
              <div className="td-stat-card">
                <div className="td-stat-value td-market-cap">
                  {marketInfo.market_cap_fmt ?? '-'}
                </div>
                <div className="td-stat-label">Market Cap</div>
              </div>
              <div className="td-stat-card">
                <div className="td-stat-value">{formatVolume(marketInfo.volume)}</div>
                <div className="td-stat-label-detail">
                  Vol / Avg {formatVolume(marketInfo.avg_volume)}
                </div>
              </div>
              <div className="td-stat-card">
                <div className="td-stat-value td-market-cap">
                  {formatPrice(marketInfo.fifty_two_week_low)} — {formatPrice(marketInfo.fifty_two_week_high)}
                </div>
                <div className="td-stat-label">52-Week Range</div>
              </div>
              <div className="td-stat-card">
                <div className="td-stat-value">
                  {marketInfo.pe_ratio != null ? marketInfo.pe_ratio.toFixed(2) : '-'}
                </div>
                <div className="td-stat-label">P/E Ratio</div>
              </div>
              <div className="td-stat-card">
                <div className="td-stat-value td-market-cap">
                  {marketInfo.sector ?? '-'}
                </div>
                <div className="td-stat-label">Sector</div>
              </div>
            </>
          )}
        </div>
      )}

      {/* Price Chart */}
      <div className="td-price-section">
        <div className="td-price-header">
          <h2>Price Chart <span style={{ fontWeight: 400, fontSize: '0.75em', opacity: 0.6 }}>(ET)</span></h2>
          <div className="td-price-controls">
            <div className="td-window-selector">
              {PRICE_RANGES.map((r) => (
                <button
                  key={r}
                  className={`window-btn ${priceRange === r ? 'active' : ''}`}
                  onClick={() => setPriceRange(r)}
                >
                  {r}
                </button>
              ))}
            </div>
            <button
              className={`td-overlay-toggle ${mentionOverlay ? 'active' : ''}`}
              onClick={() => setMentionOverlay(!mentionOverlay)}
            >
              {mentionOverlay ? 'Hide Mentions' : 'Show Mentions'}
            </button>
          </div>
        </div>

        {priceLoading && !priceData && (
          <div className="td-chart-skeleton">
            <div className="skel-line skel-chart-area" />
          </div>
        )}
        {!priceLoading && (!priceData || priceData.length === 0) && (
          <p className="td-no-data">No price data available for {ticker?.toUpperCase()}.</p>
        )}
        {priceData && priceData.length > 0 && (
          <div className="td-chart">
            <ResponsiveContainer width="100%" height={350}>
              <ComposedChart data={mergedPriceData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(var(--soft-border), 0.3)" />
                <XAxis
                  dataKey="t"
                  tickFormatter={(t) => formatPriceTs(t, priceRange)}
                  tick={{ fill: 'rgb(var(--soft-text))', fontSize: 12 }}
                  stroke="rgba(var(--soft-border), 0.5)"
                  minTickGap={40}
                />
                <YAxis
                  yAxisId="price"
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => `$${v}`}
                  tick={{ fill: 'rgb(var(--soft-text))', fontSize: 12 }}
                  stroke="rgba(var(--soft-border), 0.5)"
                />
                {mentionOverlay && (
                  <YAxis
                    yAxisId="mentions"
                    orientation="right"
                    allowDecimals={false}
                    tick={{ fill: 'rgba(var(--accent), 0.7)', fontSize: 11 }}
                    stroke="rgba(var(--accent), 0.3)"
                  />
                )}
                <Tooltip
                  contentStyle={{
                    background: 'rgb(var(--primary-color))',
                    border: '1px solid rgba(var(--soft-border), var(--soft-border-alpha))',
                    borderRadius: 8,
                    fontSize: '0.82rem',
                  }}
                  labelFormatter={(t) => formatPriceTs(t, priceRange)}
                  formatter={(value, name) => {
                    if (name === 'mentions') return [value, 'Mentions']
                    return [formatPrice(value), 'Price']
                  }}
                />
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="c"
                  name="close"
                  stroke="rgb(99, 102, 241)"
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
                {mentionOverlay && (
                  <Bar
                    yAxisId="mentions"
                    dataKey="mentions"
                    name="mentions"
                    fill="rgba(var(--accent), 0.25)"
                    stroke="rgba(var(--accent), 0.5)"
                    isAnimationActive={false}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Mention Stats Cards */}
      {data && (
        <div className="td-stats">
          {data.sentiment && (
            <div className={`td-stat-card td-sentiment-card td-sentiment-${data.sentiment.label}`}>
              <div className={`td-sentiment-score td-sentiment-score-${data.sentiment.label}`}>
                {data.sentiment.label === 'bullish' && <FiTrendingUp />}
                {data.sentiment.label === 'bearish' && <FiTrendingDown />}
                {data.sentiment.label === 'neutral' && <FiArrowRight />}
                {' '}{data.sentiment.score > 0 ? '+' : ''}{data.sentiment.score.toFixed(2)}
              </div>
              <div className="td-sentiment-label-text">{data.sentiment.label}</div>
              <div className="td-stat-label">
                Sentiment ({data.sentiment.confidence})
              </div>
              {data.sentiment.sources && Object.keys(data.sentiment.sources).length > 0 && (
                <div className="td-sentiment-sources">
                  {Object.entries(data.sentiment.sources).map(([src, count]) => (
                    <span key={src} className="td-sentiment-source-chip">
                      {src.replace('reddit_', 'r/')}: {count}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="td-stat-card">
            <div className="td-stat-value">{data.total_mentions.toLocaleString()}</div>
            <div className="td-stat-label">Total Mentions</div>
          </div>
          <div className="td-stat-card">
            <div className="td-stat-value">{data.unique_posts?.toLocaleString() ?? '-'}</div>
            <div className="td-stat-label">Unique Posts</div>
          </div>
          <div className="td-stat-card">
            <div className="td-stat-value">{topSubreddit}</div>
            <div className="td-stat-label">Top Subreddit</div>
          </div>
          <div className="td-stat-card">
            <div className="td-stat-value">{subreddits.length}</div>
            <div className="td-stat-label">Subreddits</div>
          </div>
        </div>
      )}

      {/* Mentions Over Time Chart */}
      <div className="td-chart-section">
        <h2>Mentions Over Time</h2>
        {loading && !data && (
          <div className="td-chart-skeleton">
            <div className="skel-line skel-chart-area" />
          </div>
        )}
        {data && chartData.length === 0 && (
          <p className="td-no-data">No mention data for this window.</p>
        )}
        {data && chartData.length > 0 && (
          <div className="td-chart">
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(var(--soft-border), 0.3)" />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={formatTimestamp}
                  tick={{ fill: 'rgb(var(--soft-text))', fontSize: 12 }}
                  stroke="rgba(var(--soft-border), 0.5)"
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fill: 'rgb(var(--soft-text))', fontSize: 12 }}
                  stroke="rgba(var(--soft-border), 0.5)"
                />
                <Tooltip
                  contentStyle={{
                    background: 'rgb(var(--primary-color))',
                    border: '1px solid rgba(var(--soft-border), var(--soft-border-alpha))',
                    borderRadius: 8,
                    fontSize: '0.82rem',
                  }}
                  labelFormatter={formatTimestamp}
                />
                <Legend
                  wrapperStyle={{ fontSize: '0.8rem' }}
                />
                {subreddits.map((sub, i) => (
                  <Area
                    key={sub}
                    type="monotone"
                    dataKey={sub}
                    stackId="1"
                    stroke={`rgb(${PALETTE[i % PALETTE.length]})`}
                    fill={`rgba(${PALETTE[i % PALETTE.length]}, 0.4)`}
                    isAnimationActive={false}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Subreddit Breakdown */}
      {data && subreddits.length > 0 && (
        <div className="td-sub-breakdown">
          <h2>By Subreddit</h2>
          <div className="td-sub-bars">
            {subreddits.map((sub, i) => {
              const count = data.mentions_by_subreddit[sub]
              const pct = data.total_mentions > 0 ? (count / data.total_mentions) * 100 : 0
              return (
                <div key={sub} className="td-sub-row">
                  <span className="td-sub-name">r/{sub}</span>
                  <div className="td-sub-bar-track">
                    <div
                      className="td-sub-bar-fill"
                      style={{
                        width: `${pct}%`,
                        background: `rgb(${PALETTE[i % PALETTE.length]})`,
                      }}
                    />
                  </div>
                  <span className="td-sub-count">{count}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Post Feed */}
      <PostFeed ticker={ticker} title="Recent Posts" />
    </div>
  )
}
