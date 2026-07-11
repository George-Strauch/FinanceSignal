import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ComposedChart, Line, Bar,
} from 'recharts'
import { FiArrowLeft, FiExternalLink, FiTrendingUp, FiTrendingDown, FiArrowRight, FiPlus, FiUser, FiBriefcase, FiCpu } from 'react-icons/fi'
import { get, post, del } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import PostFeed from '../components/PostFeed'
import TradeEntryModal from '../components/TradeEntryModal'
import LLMAnalysisModal from '../components/LLMAnalysisModal'
import PositionsTable from '../components/PositionsTable'
import { recordTickerVisit } from './Tickers'
import './TickerDetail.css'

const WINDOWS = ['1h', '6h', '24h', '7d', '30d']
const PRICE_RANGES = ['1D', '5D', '1M', '3M', '6M', '1Y']
const RANGE_API_MAP = { '1D': '1d', '5D': '5d', '1M': '1mo', '3M': '3mo', '6M': '6mo', '1Y': '1y' }
const COUNT_MODES = [
  { value: 'mentions', label: 'Mentions' },
  { value: 'authors', label: 'Authors' },
  { value: 'posts', label: 'Posts' },
]

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

function PastAnalysis({ id }) {
  const [analysis, setAnalysis] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    get(`/analysis/${id}`)
      .then((res) => setAnalysis(res))
      .catch(() => setAnalysis(null))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <p className="td-llm-loading">Loading...</p>
  if (!analysis) return <p className="td-llm-loading">Failed to load.</p>
  return (
    <div className="td-llm-analysis-content">
      <div className="td-llm-analysis-meta">
        <span>Model: {analysis.model}</span>
        <span>Tokens: {analysis.input_tokens || '?'}</span>
      </div>
      <div className="td-llm-analysis-response">{analysis.response}</div>
    </div>
  )
}

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
  const [searchParams] = useSearchParams()
  const dateParam = searchParams.get('date')
  const [window, setWindow] = usePersistedState('ticker-detail-window', '7d')
  const [countMode, setCountMode] = usePersistedState('count-mode', 'mentions')
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

  // Subreddit mentions chart (below price chart, shares priceRange)
  const [subMentionData, setSubMentionData] = useState(null)
  const [subMentionLoading, setSubMentionLoading] = useState(true)

  // Post feed date filter state
  const [postDateFrom, setPostDateFrom] = usePersistedState('ticker-post-date-from', null)
  const [postDateTo, setPostDateTo] = usePersistedState('ticker-post-date-to', null)

  // Bottom tab state: 'posts' or 'authors'
  const [bottomTab, setBottomTab] = usePersistedState('ticker-bottom-tab', 'posts')
  const [authorData, setAuthorData] = useState(null)
  const [authorLoading, setAuthorLoading] = useState(false)

  // Tag quick-add state
  const [allTagSets, setAllTagSets] = useState([])
  const [tagMenuOpen, setTagMenuOpen] = useState(false)
  const tagMenuRef = useRef(null)

  // Trade state
  const [tradeModalOpen, setTradeModalOpen] = useState(false)
  const [tickerTrades, setTickerTrades] = useState([])
  const [tradesLoading, setTradesLoading] = useState(true)

  // LLM analysis state
  const [llmModalOpen, setLlmModalOpen] = useState(false)
  const [llmAnalyses, setLlmAnalyses] = useState([])
  const [llmAnalysesLoading, setLlmAnalysesLoading] = useState(true)
  const [expandedAnalysis, setExpandedAnalysis] = useState(null)

  // Record visit for recent tickers
  useEffect(() => {
    if (ticker) recordTickerVisit(ticker)
  }, [ticker])

  const countModeLabel = COUNT_MODES.find((m) => m.value === countMode)?.label.toLowerCase() || 'mentions'

  // Fetch Reddit mention detail
  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ window, count_mode: countMode })
      if (dateParam) params.set('date', dateParam)
      const res = await get(`/tickers/${ticker}?${params}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [ticker, window, countMode, dateParam])

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

  // Fundamentals state (on-demand fetch)
  const [fundamentals, setFundamentals] = useState(null)
  const [fundLoading, setFundLoading] = useState(true)

  // Fetch fundamentals (triggers on-demand refresh on backend)
  const fetchFundamentals = useCallback(async () => {
    setFundLoading(true)
    try {
      const res = await get(`/fundamentals/${ticker}`)
      if (!res.error) setFundamentals(res)
    } catch {
      // fallback to market info
    } finally {
      setFundLoading(false)
    }
  }, [ticker])

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
      const res = await get(`/mentions/${ticker}/hourly?range=${rangeParam}&count_mode=${countMode}`)
      setMentionData(res.mentions)
    } catch {
      setMentionData(null)
    } finally {
      setMentionLoading(false)
    }
  }, [ticker, priceRange, countMode])

  // Fetch subreddit-level mentions for chart below price chart
  const fetchSubMentions = useCallback(async () => {
    setSubMentionLoading(true)
    try {
      const rangeParam = RANGE_API_MAP[priceRange]
      const res = await get(`/mentions/${ticker}/by-subreddit?range=${rangeParam}&count_mode=${countMode}`)
      setSubMentionData(res.mentions)
    } catch {
      setSubMentionData(null)
    } finally {
      setSubMentionLoading(false)
    }
  }, [ticker, priceRange, countMode])

  // Fetch top authors for this ticker
  const fetchAuthors = useCallback(async () => {
    setAuthorLoading(true)
    try {
      const params = new URLSearchParams({ window })
      if (dateParam) params.set('date', dateParam)
      const res = await get(`/tickers/${ticker}/authors?${params}`)
      setAuthorData(res)
    } catch {
      setAuthorData(null)
    } finally {
      setAuthorLoading(false)
    }
  }, [ticker, window, dateParam])

  // Fetch open trades for this ticker
  const fetchTickerTrades = useCallback(async () => {
    setTradesLoading(true)
    try {
      const res = await get(`/trading/ticker/${ticker}/trades`)
      setTickerTrades(res.trades || [])
    } catch {
      setTickerTrades([])
    } finally {
      setTradesLoading(false)
    }
  }, [ticker])

  const fetchLlmAnalyses = useCallback(async () => {
    setLlmAnalysesLoading(true)
    try {
      const res = await get(`/analysis/history/${ticker}`)
      setLlmAnalyses(res.analyses || [])
    } catch {
      setLlmAnalyses([])
    } finally {
      setLlmAnalysesLoading(false)
    }
  }, [ticker])

  useEffect(() => { fetchTickerTrades() }, [fetchTickerTrades])
  useEffect(() => { fetchLlmAnalyses() }, [fetchLlmAnalyses])
  useEffect(() => { fetchDetail() }, [fetchDetail])
  useEffect(() => {
    if (bottomTab === 'authors') fetchAuthors()
  }, [bottomTab, fetchAuthors])
  useEffect(() => { fetchPriceChart() }, [fetchPriceChart])
  useEffect(() => { fetchMarketInfo() }, [fetchMarketInfo])
  useEffect(() => { fetchFundamentals() }, [fetchFundamentals])
  useEffect(() => { fetchSubMentions() }, [fetchSubMentions])
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
  const topSubreddit = subreddits.length > 0 ? subreddits[0] : '-'

  // Merged data for price chart with optional mention overlay
  const mergedPriceData = priceData
    ? (mentionOverlay && mentionData
        ? mergePriceMentions(priceData, mentionData, priceRange)
        : priceData)
    : []

  // Pivot subreddit mention data for stacked area chart
  const subMentionSubs = subMentionData
    ? [...new Set(subMentionData.map((r) => r.subreddit))]
    : []
  const subMentionChartData = subMentionData
    ? pivotChartData(subMentionData, subMentionSubs)
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
            <button
              className="td-trade-btn"
              onClick={() => setTradeModalOpen(true)}
              title="Open a paper trade"
            >
              <FiBriefcase /> Trade
            </button>
            <button
              className="td-llm-btn"
              onClick={() => setLlmModalOpen(true)}
              title="Analyze posts with LLM"
            >
              <FiCpu /> Analyze
            </button>
          </div>
          {data && (
            <span className="td-mention-count">
              {data.total_mentions.toLocaleString()} {countModeLabel}
            </span>
          )}
        </div>
        <div className="td-count-mode-selector">
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

      {dateParam && (
        <div className="td-date-banner">
          Historical view: {new Date(dateParam + 'T00:00:00').toLocaleDateString('en-US', {
            weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
          })}
          <button className="td-date-clear" onClick={() => navigate(`/tickers/${ticker}`)}>
            Back to live
          </button>
        </div>
      )}

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

      {/* Open Positions for this ticker */}
      {tickerTrades.length > 0 && (
        <div className="td-positions-section">
          <h2>Open Positions</h2>
          <PositionsTable
            trades={tickerTrades}
            showStrategy={true}
            compact={true}
            onTradeUpdated={fetchTickerTrades}
          />
        </div>
      )}

      {/* Fundamentals Panel */}
      {fundamentals && !fundLoading && (
        <div className="td-fundamentals-panel">
          <h2>Fundamentals</h2>
          <div className="td-fund-grid">
            <div className="td-fund-section">
              <h3>Valuation</h3>
              <div className="td-fund-rows">
                <div className="td-fund-row"><span>P/E (TTM)</span><span>{fundamentals.pe_trailing?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>P/E (Fwd)</span><span>{fundamentals.pe_forward?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>PEG Ratio</span><span>{fundamentals.peg_ratio?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>P/B</span><span>{fundamentals.price_to_book?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>P/S</span><span>{fundamentals.price_to_sales?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>EV/EBITDA</span><span>{fundamentals.ev_to_ebitda?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>EV/Revenue</span><span>{fundamentals.ev_to_revenue?.toFixed(2) ?? '-'}</span></div>
              </div>
            </div>
            <div className="td-fund-section">
              <h3>Profitability</h3>
              <div className="td-fund-rows">
                <div className="td-fund-row"><span>Profit Margin</span><span>{fundamentals.profit_margin != null ? (fundamentals.profit_margin * 100).toFixed(1) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>Operating Margin</span><span>{fundamentals.operating_margin != null ? (fundamentals.operating_margin * 100).toFixed(1) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>Gross Margin</span><span>{fundamentals.gross_margin != null ? (fundamentals.gross_margin * 100).toFixed(1) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>ROE</span><span>{fundamentals.return_on_equity != null ? (fundamentals.return_on_equity * 100).toFixed(1) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>ROA</span><span>{fundamentals.return_on_assets != null ? (fundamentals.return_on_assets * 100).toFixed(1) + '%' : '-'}</span></div>
              </div>
            </div>
            <div className="td-fund-section">
              <h3>Growth</h3>
              <div className="td-fund-rows">
                <div className="td-fund-row"><span>Revenue</span><span>{fundamentals.revenue_fmt ?? '-'}</span></div>
                <div className="td-fund-row"><span>Revenue Growth</span><span>{fundamentals.revenue_growth != null ? (fundamentals.revenue_growth * 100).toFixed(1) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>Earnings Growth</span><span>{fundamentals.earnings_growth != null ? (fundamentals.earnings_growth * 100).toFixed(1) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>EPS (TTM)</span><span>{fundamentals.eps_trailing != null ? '$' + fundamentals.eps_trailing.toFixed(2) : '-'}</span></div>
                <div className="td-fund-row"><span>EPS (Fwd)</span><span>{fundamentals.eps_forward != null ? '$' + fundamentals.eps_forward.toFixed(2) : '-'}</span></div>
              </div>
            </div>
            <div className="td-fund-section">
              <h3>Balance Sheet</h3>
              <div className="td-fund-rows">
                <div className="td-fund-row"><span>Total Cash</span><span>{fundamentals.total_cash_fmt ?? '-'}</span></div>
                <div className="td-fund-row"><span>Total Debt</span><span>{fundamentals.total_debt_fmt ?? '-'}</span></div>
                <div className="td-fund-row"><span>D/E Ratio</span><span>{fundamentals.debt_to_equity?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>Current Ratio</span><span>{fundamentals.current_ratio?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>Book Value</span><span>{fundamentals.book_value != null ? '$' + fundamentals.book_value.toFixed(2) : '-'}</span></div>
              </div>
            </div>
            <div className="td-fund-section">
              <h3>Dividends</h3>
              <div className="td-fund-rows">
                <div className="td-fund-row"><span>Yield</span><span>{fundamentals.dividend_yield != null ? (fundamentals.dividend_yield * 100).toFixed(2) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>Rate</span><span>{fundamentals.dividend_rate != null ? '$' + fundamentals.dividend_rate.toFixed(2) : '-'}</span></div>
                <div className="td-fund-row"><span>Payout Ratio</span><span>{fundamentals.payout_ratio != null ? (fundamentals.payout_ratio * 100).toFixed(1) + '%' : '-'}</span></div>
                <div className="td-fund-row"><span>Ex-Div Date</span><span>{fundamentals.ex_dividend_date ?? '-'}</span></div>
              </div>
            </div>
            <div className="td-fund-section">
              <h3>Trading</h3>
              <div className="td-fund-rows">
                <div className="td-fund-row"><span>Beta</span><span>{fundamentals.beta?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>50-Day Avg</span><span>{fundamentals.fifty_day_avg != null ? '$' + fundamentals.fifty_day_avg.toFixed(2) : '-'}</span></div>
                <div className="td-fund-row"><span>200-Day Avg</span><span>{fundamentals.two_hundred_day_avg != null ? '$' + fundamentals.two_hundred_day_avg.toFixed(2) : '-'}</span></div>
                <div className="td-fund-row"><span>Short Ratio</span><span>{fundamentals.short_ratio?.toFixed(2) ?? '-'}</span></div>
                <div className="td-fund-row"><span>Short % Float</span><span>{fundamentals.short_pct_of_float != null ? (fundamentals.short_pct_of_float * 100).toFixed(2) + '%' : '-'}</span></div>
              </div>
            </div>
          </div>
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
              {mentionOverlay ? `Hide ${COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions'}` : `Show ${COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions'}`}
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
                    if (name === 'mentions') return [value, COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions']
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

      {/* Mentions by Subreddit (synced with price chart range) */}
      <div className="td-chart-section">
        <h2>{COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions'} by Subreddit</h2>
        {subMentionLoading && !subMentionData && (
          <div className="td-chart-skeleton">
            <div className="skel-line skel-chart-area" />
          </div>
        )}
        {!subMentionLoading && (!subMentionData || subMentionChartData.length === 0) && (
          <p className="td-no-data">No mention data for this range.</p>
        )}
        {subMentionChartData.length > 0 && (
          <div className="td-chart">
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={subMentionChartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(var(--soft-border), 0.3)" />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(t) => formatPriceTs(t, priceRange)}
                  tick={{ fill: 'rgb(var(--soft-text))', fontSize: 12 }}
                  stroke="rgba(var(--soft-border), 0.5)"
                  minTickGap={40}
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
                  labelFormatter={(t) => formatPriceTs(t, priceRange)}
                />
                <Legend
                  wrapperStyle={{ fontSize: '0.8rem' }}
                />
                {subMentionSubs.map((sub, i) => (
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
            <div className="td-stat-label">Total {COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions'}</div>
          </div>
          {countMode !== 'posts' && (
            <div className="td-stat-card">
              <div className="td-stat-value">{data.unique_posts?.toLocaleString() ?? '-'}</div>
              <div className="td-stat-label">Unique Posts</div>
            </div>
          )}
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

      {/* Bottom Tabs: Posts / By Author */}
      <div className="td-bottom-tabs">
        <button
          className={`td-tab-btn ${bottomTab === 'posts' ? 'active' : ''}`}
          onClick={() => setBottomTab('posts')}
        >
          Posts
        </button>
        <button
          className={`td-tab-btn ${bottomTab === 'authors' ? 'active' : ''}`}
          onClick={() => setBottomTab('authors')}
        >
          <FiUser /> By Author
        </button>
      </div>

      {bottomTab === 'posts' && (
        <PostFeed
          ticker={ticker}
          title="Posts"
          dateFrom={postDateFrom}
          dateTo={postDateTo}
          onDateChange={(from, to) => { setPostDateFrom(from); setPostDateTo(to) }}
        />
      )}

      {bottomTab === 'authors' && (
        <div className="td-author-section">
          {authorLoading && !authorData && (
            <div className="td-author-skeleton">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="td-author-row skeleton">
                  <div className="skel-line" style={{ height: 18, width: '40%' }} />
                  <div className="skel-line" style={{ height: 14, width: '20%' }} />
                </div>
              ))}
            </div>
          )}
          {!authorLoading && (!authorData?.authors || authorData.authors.length === 0) && (
            <p className="td-no-data">No author data for this window.</p>
          )}
          {authorData?.authors?.length > 0 && (
            <div className="td-author-list">
              {authorData.authors.map((a, i) => {
                const maxCount = authorData.authors[0].total_count
                const pct = maxCount > 0 ? (a.total_count / maxCount) * 100 : 0
                return (
                  <div
                    key={a.author}
                    className="td-author-row"
                    onClick={() => navigate(`/authors/${encodeURIComponent(a.author)}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && navigate(`/authors/${encodeURIComponent(a.author)}`)}
                  >
                    <span className="td-author-rank">{i + 1}</span>
                    <span className="td-author-name">u/{a.author}</span>
                    <div className="td-author-bar-track">
                      <div className="td-author-bar-fill" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="td-author-counts">
                      {a.post_count}p / {a.comment_count}c
                    </span>
                    <span className="td-author-total">{a.total_count}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Past LLM Analyses */}
      {llmAnalyses.length > 0 && (
        <div className="td-llm-analyses-section">
          <h2>LLM Analyses</h2>
          <div className="td-llm-analyses-list">
            {llmAnalyses.map((a) => (
              <div
                key={a.id}
                className={`td-llm-analysis-item ${expandedAnalysis === a.id ? 'expanded' : ''}`}
                onClick={() => setExpandedAnalysis(expandedAnalysis === a.id ? null : a.id)}
              >
                <div className="td-llm-analysis-header">
                  <span className="td-llm-analysis-model">{a.model.split('/').pop()}</span>
                  <span className="td-llm-analysis-date">
                    {new Date(a.created_at_iso).toLocaleString()}
                  </span>
                  <span className="td-llm-analysis-posts">{a.post_count} posts</span>
                </div>
                {expandedAnalysis === a.id && (
                  <div className="td-llm-analysis-body">
                    <PastAnalysis id={a.id} />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <TradeEntryModal
        isOpen={tradeModalOpen}
        onClose={() => setTradeModalOpen(false)}
        onTradeOpened={fetchTickerTrades}
        prefillTicker={ticker?.toUpperCase()}
        prefillPrice={marketInfo?.current_price ? String(marketInfo.current_price) : ''}
      />

      <LLMAnalysisModal
        isOpen={llmModalOpen}
        onClose={() => {
          setLlmModalOpen(false)
          fetchLlmAnalyses()
        }}
        ticker={ticker?.toUpperCase()}
        prefillDateFrom={postDateFrom}
        prefillDateTo={postDateTo}
      />
    </div>
  )
}
