import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { FiArrowLeft } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import PostFeed from '../components/PostFeed'
import './SubredditDetail.css'

const WINDOWS = ['7d', '30d', '90d', 'all']
const AUTHOR_SORTS = [
  { key: 'combined', label: 'Combined' },
  { key: 'post_count', label: 'Posts' },
  { key: 'comment_count', label: 'Comments' },
  { key: 'avg_post_score', label: 'Avg Score' },
]

function formatTimestamp(ts) {
  if (!ts) return ''
  if (ts.length <= 10) return ts
  return ts.slice(5, 10) + ' ' + ts.slice(11, 16)
}

function formatNum(n) {
  if (n == null) return '\u2014'
  return Number(n).toLocaleString()
}

function formatFetchTime(iso) {
  if (!iso) return '\u2014'
  const d = new Date(iso)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function SubredditDetail() {
  const { subreddit } = useParams()
  const navigate = useNavigate()
  const [window, setWindow] = usePersistedState('subreddit-detail-window', '30d')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Activity chart
  const [activityData, setActivityData] = useState(null)
  const [activityLoading, setActivityLoading] = useState(true)

  // Authors
  const [authors, setAuthors] = useState(null)
  const [authorsLoading, setAuthorsLoading] = useState(true)
  const [authorSort, setAuthorSort] = usePersistedState('subreddit-author-sort', 'combined')

  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await get(`/reddit-stats/subreddit/${subreddit}?window=${window}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [subreddit, window])

  const fetchActivity = useCallback(async () => {
    setActivityLoading(true)
    try {
      const res = await get(`/reddit-stats/activity?window=${window}&subreddit=${subreddit}`)
      setActivityData(res)
    } catch { setActivityData(null) }
    finally { setActivityLoading(false) }
  }, [subreddit, window])

  const fetchAuthors = useCallback(async () => {
    setAuthorsLoading(true)
    try {
      const res = await get(`/reddit-stats/top-authors?window=${window}&subreddit=${subreddit}&sort_by=${authorSort}&limit=15`)
      setAuthors(res)
    } catch { setAuthors(null) }
    finally { setAuthorsLoading(false) }
  }, [subreddit, window, authorSort])

  useEffect(() => { fetchDetail() }, [fetchDetail])
  useEffect(() => { fetchActivity() }, [fetchActivity])
  useEffect(() => { fetchAuthors() }, [fetchAuthors])

  return (
    <div className="subreddit-detail">
      <nav className="breadcrumb">
        <Link to="/sources" className="breadcrumb-link">Sources</Link>
        <span className="breadcrumb-sep">/</span>
        <Link to="/sources/reddit" className="breadcrumb-link">Reddit</Link>
        <span className="breadcrumb-sep">/</span>
        <span className="breadcrumb-current">r/{subreddit}</span>
      </nav>

      <div className="sd-header">
        <button className="sd-back" onClick={() => navigate(-1)}>
          <FiArrowLeft />
        </button>
        <div className="sd-title-group">
          <h1 className="sd-name">r/{subreddit}</h1>
          {data && (
            <span className="sd-post-count">
              {formatNum(data.total_posts)} posts
            </span>
          )}
        </div>
        <div className="sd-window-selector">
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

      {error && <div className="sd-error">Failed to load: {error}</div>}

      {/* Stat Cards */}
      <div className="sd-stats">
        {loading && !data ? (
          [...Array(6)].map((_, i) => (
            <div key={i} className="sd-stat-card">
              <div className="skel-line" style={{ height: 28, width: '60%', margin: '0 auto 6px' }} />
              <div className="skel-line" style={{ height: 12, width: '50%', margin: '0 auto' }} />
            </div>
          ))
        ) : data && (
          <>
            <div className="sd-stat-card">
              <div className="sd-stat-value">{formatNum(data.total_posts)}</div>
              <div className="sd-stat-label">Posts</div>
            </div>
            <div className="sd-stat-card">
              <div className="sd-stat-value">{formatNum(data.total_comments)}</div>
              <div className="sd-stat-label">Comments</div>
            </div>
            <div className="sd-stat-card">
              <div className="sd-stat-value">{formatNum(data.unique_authors)}</div>
              <div className="sd-stat-label">Unique Authors</div>
            </div>
            <div className="sd-stat-card">
              <div className="sd-stat-value">{data.avg_score}</div>
              <div className="sd-stat-label">Avg Score</div>
            </div>
            <div className="sd-stat-card">
              <div className="sd-stat-value">{data.avg_comments_per_post}</div>
              <div className="sd-stat-label">Avg Comments/Post</div>
            </div>
            <div className="sd-stat-card">
              <div className="sd-stat-value sd-stat-flair">{data.top_flair || '\u2014'}</div>
              <div className="sd-stat-label">Top Flair</div>
            </div>
          </>
        )}
      </div>

      {/* Activity Chart */}
      <div className="sd-chart-section">
        <h2>Activity Over Time</h2>
        {activityLoading && !activityData && (
          <div className="sd-chart-skeleton">
            <div className="skel-line skel-chart-area" />
          </div>
        )}
        {activityData && activityData.timeline.length === 0 && (
          <p className="sd-no-data">No activity data for this window.</p>
        )}
        {activityData && activityData.timeline.length > 0 && (
          <div className="sd-chart">
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={activityData.timeline} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(var(--soft-border), 0.3)" />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={formatTimestamp}
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
                  labelFormatter={formatTimestamp}
                />
                <Legend wrapperStyle={{ fontSize: '0.8rem' }} />
                <Area
                  type="monotone"
                  dataKey="posts"
                  stackId="1"
                  stroke="rgb(99, 102, 241)"
                  fill="rgba(99, 102, 241, 0.4)"
                  isAnimationActive={false}
                />
                <Area
                  type="monotone"
                  dataKey="comments"
                  stackId="1"
                  stroke="rgb(244, 114, 182)"
                  fill="rgba(244, 114, 182, 0.4)"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Top Contributors */}
      <div className="sd-authors-section">
        <div className="sd-authors-header">
          <h2>Top Contributors</h2>
          <div className="sd-author-sort-tabs">
            {AUTHOR_SORTS.map((s) => (
              <button
                key={s.key}
                className={`sd-sort-tab ${authorSort === s.key ? 'active' : ''}`}
                onClick={() => setAuthorSort(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
        {authorsLoading && !authors && (
          <div className="sd-authors-skeleton">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="skel-line" style={{ height: 36, marginBottom: 6 }} />
            ))}
          </div>
        )}
        {authors && authors.authors.length === 0 && (
          <p className="sd-no-data">No author data for this window.</p>
        )}
        {authors && authors.authors.length > 0 && (
          <div className="sd-authors-table-wrap">
            <table className="sd-authors-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Author</th>
                  <th>Posts</th>
                  <th>Comments</th>
                  <th>Combined</th>
                  <th>Avg Score</th>
                </tr>
              </thead>
              <tbody>
                {authors.authors.map((a, i) => (
                  <tr key={a.author}>
                    <td className="sd-rank">{i + 1}</td>
                    <td className="sd-author-name">
                      <Link to={`/authors/${a.author}`}>u/{a.author}</Link>
                    </td>
                    <td>{a.post_count.toLocaleString()}</td>
                    <td>{a.comment_count.toLocaleString()}</td>
                    <td className="sd-combined">{a.combined.toLocaleString()}</td>
                    <td>{a.avg_post_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Fetch History */}
      {data && data.fetch_history.length > 0 && (
        <div className="sd-fetch-section">
          <h2>Recent Fetch History</h2>
          <div className="sd-fetch-table-wrap">
            <table className="sd-fetch-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Endpoint</th>
                  <th>Fetched</th>
                  <th>New</th>
                  <th>Updated</th>
                  <th>Duration</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {data.fetch_history.map((fh, i) => (
                  <tr key={i}>
                    <td>{fh.fetch_type}</td>
                    <td className="sd-fetch-endpoint">{fh.endpoint}</td>
                    <td>{fh.items_fetched}</td>
                    <td>{fh.items_new}</td>
                    <td>{fh.items_updated}</td>
                    <td>{fh.duration_seconds != null ? `${fh.duration_seconds}s` : '\u2014'}</td>
                    <td title={fh.fetched_at || ''}>{formatFetchTime(fh.fetched_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Post Feed */}
      <PostFeed
        subreddit={subreddit}
        title={`r/${subreddit} Posts`}
      />
    </div>
  )
}
