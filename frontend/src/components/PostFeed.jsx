import { useEffect, useState, useCallback, useMemo } from 'react'
import { FiChevronLeft, FiChevronRight, FiAlertCircle, FiRefreshCw, FiX } from 'react-icons/fi'
import { get } from '../api/client'
import PostCard from './PostCard'
import './PostFeed.css'

const SORT_LABELS = {
  date: 'Newest',
  score: 'Top',
  comments: 'Most Comments',
  relevance: 'Relevance',
}

const DATE_PRESETS = [
  { label: '24h', seconds: 86400 },
  { label: '7d', seconds: 604800 },
  { label: '30d', seconds: 2592000 },
  { label: '90d', seconds: 7776000 },
  { label: 'All', seconds: null },
]

/** Convert a unix timestamp (seconds) to YYYY-MM-DD in local time */
function tsToDateStr(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Convert YYYY-MM-DD to unix timestamp (start of day, local time) */
function dateStrToTs(str) {
  if (!str) return null
  const [y, m, d] = str.split('-').map(Number)
  return new Date(y, m - 1, d).getTime() / 1000
}

/** Convert YYYY-MM-DD to unix timestamp (end of day, local time) */
function dateStrToTsEnd(str) {
  if (!str) return null
  const [y, m, d] = str.split('-').map(Number)
  return new Date(y, m - 1, d, 23, 59, 59).getTime() / 1000
}

export default function PostFeed({
  ticker,
  subreddit,
  entity,
  author,
  perPage = 25,
  highlightTicker,
  title = 'Recent Posts',
  dateFrom,
  dateTo,
  onDateChange,
}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('date')

  const fetchPosts = useCallback(async () => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (ticker) params.set('ticker', ticker)
    if (subreddit) params.set('subreddit', subreddit)
    if (entity) params.set('entity', entity)
    if (author) params.set('author', author)
    if (dateFrom != null) params.set('date_from', dateFrom)
    if (dateTo != null) params.set('date_to', dateTo)
    params.set('page', page)
    params.set('per_page', perPage)
    params.set('sort', sort)
    try {
      const res = await get(`/posts?${params}`)
      setData(res)
    } catch (err) {
      setError(err.message)
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [ticker, subreddit, entity, author, page, perPage, sort, dateFrom, dateTo])

  useEffect(() => { fetchPosts() }, [fetchPosts])
  useEffect(() => { setPage(1) }, [sort, ticker, subreddit, entity, author, dateFrom, dateTo])
  useEffect(() => {
    if (sort === 'relevance' && !ticker && !entity) setSort('date')
  }, [sort, ticker, entity])

  /** Detect which preset is currently active */
  const activePreset = useMemo(() => {
    if (dateFrom == null && dateTo == null) return 'All'
    if (dateTo != null) return null // custom range
    const now = Date.now() / 1000
    for (const p of DATE_PRESETS) {
      if (p.seconds == null) continue
      // Allow 60s tolerance for "now" drift
      if (Math.abs((now - p.seconds) - dateFrom) < 60) return p.label
    }
    return null
  }, [dateFrom, dateTo])

  const pagination = data?.pagination

  return (
    <div className="post-feed">
      <div className="post-feed-header">
        <h2 className="post-feed-title">{title}</h2>
        <div className="post-feed-sort">
          {Object.entries(SORT_LABELS).map(([key, label]) => {
            if (key === 'relevance' && !ticker && !entity) return null
            return (
              <button
                key={key}
                className={`pf-sort-btn ${sort === key ? 'active' : ''}`}
                onClick={() => setSort(key)}
                disabled={key === 'relevance' && !ticker && !entity}
              >
                {label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Date filter bar — only when parent provides onDateChange */}
      {onDateChange && (
        <div className="pf-date-bar">
          <div className="pf-date-presets">
            {DATE_PRESETS.map(({ label, seconds }) => (
              <button
                key={label}
                className={`pf-sort-btn ${activePreset === label ? 'active' : ''}`}
                onClick={() => {
                  if (seconds == null) {
                    onDateChange(null, null)
                  } else {
                    onDateChange(Math.floor(Date.now() / 1000) - seconds, null)
                  }
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="pf-date-custom">
            <label className="pf-date-label">
              From
              <input
                type="date"
                className="pf-date-input"
                value={dateFrom != null ? tsToDateStr(dateFrom) : ''}
                onChange={(e) => {
                  const ts = dateStrToTs(e.target.value)
                  onDateChange(ts, dateTo)
                }}
              />
            </label>
            <label className="pf-date-label">
              To
              <input
                type="date"
                className="pf-date-input"
                value={dateTo != null ? tsToDateStr(dateTo) : ''}
                onChange={(e) => {
                  const ts = dateStrToTsEnd(e.target.value)
                  onDateChange(dateFrom, ts)
                }}
              />
            </label>
          </div>
          {(dateFrom != null || dateTo != null) && (
            <button
              className="pf-date-clear"
              onClick={() => onDateChange(null, null)}
              title="Clear date filter"
            >
              <FiX /> Clear
            </button>
          )}
        </div>
      )}

      {/* Loading skeleton */}
      {loading && !data && (
        <div className="post-feed-skeleton">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="pf-skel-card">
              <div className="pf-skel-line pf-skel-title" />
              <div className="pf-skel-line pf-skel-preview" />
              <div className="pf-skel-line pf-skel-meta" />
            </div>
          ))}
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="post-feed-error">
          <FiAlertCircle className="pf-error-icon" />
          <span>Failed to load posts: {error}</span>
          <button className="pf-retry-btn" onClick={fetchPosts}>
            <FiRefreshCw /> Retry
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && data?.posts?.length === 0 && (
        <p className="post-feed-empty">No posts found{ticker ? ` mentioning ${ticker.toUpperCase()}` : ''}.</p>
      )}

      {/* Post list */}
      {data?.posts?.length > 0 && (
        <>
          <div className={`post-feed-list ${loading ? 'pf-refetching' : ''}`}>
            {data.posts.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                highlightTicker={highlightTicker || ticker}
              />
            ))}
          </div>

          {pagination && pagination.total_pages > 1 && (
            <div className="post-feed-pagination">
              <button
                className="pf-page-btn"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                <FiChevronLeft />
              </button>
              <span className="pf-page-info">
                Page {pagination.page} of {pagination.total_pages}
                <span className="pf-total-posts"> ({pagination.total_posts} posts)</span>
              </span>
              <button
                className="pf-page-btn"
                disabled={page >= pagination.total_pages}
                onClick={() => setPage((p) => p + 1)}
              >
                <FiChevronRight />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
