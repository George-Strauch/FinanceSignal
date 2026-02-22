import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { FiArrowLeft, FiExternalLink, FiChevronLeft, FiChevronRight } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import './TickerDetail.css'

const WINDOWS = ['1h', '6h', '24h', '7d', '30d']

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
  if (ts.length <= 10) return ts
  return ts.slice(5, 16)
}

function formatRelativeTime(epoch) {
  if (!epoch) return ''
  const diff = Date.now() / 1000 - epoch
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export default function TickerDetail() {
  const { ticker } = useParams()
  const navigate = useNavigate()
  const [window, setWindow] = usePersistedState('ticker-detail-window', '7d')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Posts state
  const [posts, setPosts] = useState(null)
  const [postsLoading, setPostsLoading] = useState(true)
  const [postsPage, setPostsPage] = useState(1)
  const [postsSort, setPostsSort] = usePersistedState('ticker-posts-sort', 'date')

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

  const fetchPosts = useCallback(async () => {
    setPostsLoading(true)
    try {
      const res = await get(`/posts?ticker=${ticker}&page=${postsPage}&sort=${postsSort}`)
      setPosts(res)
    } catch {
      setPosts(null)
    } finally {
      setPostsLoading(false)
    }
  }, [ticker, postsPage, postsSort])

  useEffect(() => { fetchDetail() }, [fetchDetail])
  useEffect(() => { fetchPosts() }, [fetchPosts])
  useEffect(() => { setPostsPage(1) }, [postsSort])

  const subreddits = data ? Object.keys(data.mentions_by_subreddit) : []
  const chartData = data ? pivotChartData(data.mentions_over_time, subreddits) : []
  const topSubreddit = subreddits.length > 0 ? subreddits[0] : '-'
  const pagination = posts?.pagination

  return (
    <div className="ticker-detail">
      <div className="td-header">
        <button className="td-back" onClick={() => navigate(-1)}>
          <FiArrowLeft />
        </button>
        <div className="td-title-group">
          <h1 className="td-ticker">{ticker?.toUpperCase()}</h1>
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

      {/* Stats Cards */}
      {data && (
        <div className="td-stats">
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

      {/* Chart */}
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
      <div className="td-posts-section">
        <div className="td-posts-header">
          <h2>Recent Posts</h2>
          <div className="td-posts-sort">
            {['date', 'score', 'comments'].map((s) => (
              <button
                key={s}
                className={`td-sort-btn ${postsSort === s ? 'active' : ''}`}
                onClick={() => setPostsSort(s)}
              >
                {s === 'date' ? 'Newest' : s === 'score' ? 'Top' : 'Most Comments'}
              </button>
            ))}
          </div>
        </div>

        {postsLoading && !posts && (
          <div className="td-posts-skeleton">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="td-post-skel">
                <div className="skel-line skel-post-title" />
                <div className="skel-line skel-post-meta" />
              </div>
            ))}
          </div>
        )}

        {posts && posts.posts.length === 0 && (
          <p className="td-no-data">No posts found mentioning {ticker?.toUpperCase()}.</p>
        )}

        {posts && posts.posts.length > 0 && (
          <>
            <div className="td-post-list">
              {posts.posts.map((post) => (
                <div key={post.id} className="td-post-card">
                  <div className="td-post-title-row">
                    <span className="td-post-title">{post.title}</span>
                    <a
                      href={post.reddit_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="td-post-link"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <FiExternalLink />
                    </a>
                  </div>
                  {post.selftext_preview && (
                    <p className="td-post-preview">{post.selftext_preview}</p>
                  )}
                  <div className="td-post-meta">
                    <span className="td-post-sub">r/{post.subreddit}</span>
                    <span className="td-post-author">u/{post.author}</span>
                    <span>{post.score} pts</span>
                    <span>{post.num_comments} comments</span>
                    <span>{formatRelativeTime(post.created_utc)}</span>
                  </div>
                  {post.tickers_mentioned.length > 0 && (
                    <div className="td-post-tickers">
                      {post.tickers_mentioned.map((t) => (
                        <Link
                          key={t}
                          to={`/tickers/${t}`}
                          className={`td-ticker-chip ${t === ticker?.toUpperCase() ? 'current' : ''}`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {t}
                        </Link>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {pagination && pagination.total_pages > 1 && (
              <div className="td-pagination">
                <button
                  className="td-page-btn"
                  disabled={postsPage <= 1}
                  onClick={() => setPostsPage((p) => p - 1)}
                >
                  <FiChevronLeft />
                </button>
                <span className="td-page-info">
                  Page {pagination.page} of {pagination.total_pages}
                </span>
                <button
                  className="td-page-btn"
                  disabled={postsPage >= pagination.total_pages}
                  onClick={() => setPostsPage((p) => p + 1)}
                >
                  <FiChevronRight />
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
