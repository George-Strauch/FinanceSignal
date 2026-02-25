import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { FiArrowLeft } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import PostFeed from '../components/PostFeed'
import './AuthorDetail.css'

const WINDOWS = ['7d', '30d', '90d', 'all']

const PALETTE = [
  '99, 102, 241',
  '244, 114, 182',
  '52, 211, 153',
  '251, 191, 36',
  '96, 165, 250',
  '167, 139, 250',
  '248, 113, 113',
  '45, 212, 191',
  '253, 186, 116',
  '156, 163, 175',
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

function formatDate(iso) {
  if (!iso) return '\u2014'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function AuthorDetail() {
  const { username } = useParams()
  const navigate = useNavigate()
  const [window, setWindow] = usePersistedState('author-detail-window', '30d')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await get(`/reddit-stats/author/${encodeURIComponent(username)}?window=${window}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [username, window])

  useEffect(() => { fetchDetail() }, [fetchDetail])

  return (
    <div className="author-detail">
      <nav className="breadcrumb">
        <Link to="/sources/reddit" className="breadcrumb-link">Reddit</Link>
        <span className="breadcrumb-sep">/</span>
        <span className="breadcrumb-current">u/{username}</span>
      </nav>

      <div className="ad-header">
        <button className="ad-back" onClick={() => navigate(-1)}>
          <FiArrowLeft />
        </button>
        <div className="ad-title-group">
          <h1 className="ad-name">u/{username}</h1>
          {data && (
            <span className="ad-subtitle">
              {formatNum(data.total_posts + data.total_comments)} total contributions
            </span>
          )}
        </div>
        <div className="ad-window-selector">
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

      {error && <div className="ad-error">Failed to load: {error}</div>}

      {/* Stat Cards */}
      <div className="ad-stats">
        {loading && !data ? (
          [...Array(6)].map((_, i) => (
            <div key={i} className="ad-stat-card">
              <div className="skel-line" style={{ height: 28, width: '60%', margin: '0 auto 6px' }} />
              <div className="skel-line" style={{ height: 12, width: '50%', margin: '0 auto' }} />
            </div>
          ))
        ) : data && (
          <>
            <div className="ad-stat-card">
              <div className="ad-stat-value">{formatNum(data.total_posts)}</div>
              <div className="ad-stat-label">Posts</div>
            </div>
            <div className="ad-stat-card">
              <div className="ad-stat-value">{formatNum(data.total_comments)}</div>
              <div className="ad-stat-label">Comments</div>
            </div>
            <div className="ad-stat-card">
              <div className="ad-stat-value">{data.avg_post_score}</div>
              <div className="ad-stat-label">Avg Post Score</div>
            </div>
            <div className="ad-stat-card">
              <div className="ad-stat-value">{data.avg_comment_score}</div>
              <div className="ad-stat-label">Avg Comment Score</div>
            </div>
            <div className="ad-stat-card">
              <div className="ad-stat-value">{formatNum(data.unique_subreddits)}</div>
              <div className="ad-stat-label">Subreddits</div>
            </div>
            <div className="ad-stat-card">
              <div className="ad-stat-value ad-stat-date">{formatDate(data.first_seen)}</div>
              <div className="ad-stat-label">First Seen</div>
            </div>
          </>
        )}
      </div>

      {/* Activity Chart */}
      <div className="ad-chart-section">
        <h2>Activity Over Time</h2>
        {loading && !data && (
          <div className="ad-chart-skeleton">
            <div className="skel-line skel-chart-area" />
          </div>
        )}
        {data && data.activity_timeline.length === 0 && (
          <p className="ad-no-data">No activity data for this window.</p>
        )}
        {data && data.activity_timeline.length > 0 && (
          <div className="ad-chart">
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={data.activity_timeline} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
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

      {/* Top Subreddits Breakdown */}
      {data && data.top_subreddits.length > 0 && (
        <div className="ad-sub-breakdown">
          <h2>Top Subreddits</h2>
          <div className="ad-sub-bars">
            {data.top_subreddits.map((sub, i) => {
              const total = sub.post_count + sub.comment_count
              const maxTotal = data.top_subreddits[0].post_count + data.top_subreddits[0].comment_count
              const pct = maxTotal > 0 ? (total / maxTotal) * 100 : 0
              return (
                <div key={sub.subreddit} className="ad-sub-row">
                  <Link to={`/sources/reddit/${sub.subreddit}`} className="ad-sub-name">
                    r/{sub.subreddit}
                  </Link>
                  <div className="ad-sub-bar-track">
                    <div
                      className="ad-sub-bar-fill"
                      style={{
                        width: `${pct}%`,
                        background: `rgb(${PALETTE[i % PALETTE.length]})`,
                      }}
                    />
                  </div>
                  <span className="ad-sub-counts">
                    {sub.post_count}p / {sub.comment_count}c
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Top Flairs */}
      {data && data.top_flairs.length > 0 && (
        <div className="ad-flairs-section">
          <h2>Top Flairs</h2>
          <div className="ad-flair-list">
            {data.top_flairs.map((f) => (
              <span key={f.flair} className="ad-flair-chip">
                {f.flair} <span className="ad-flair-count">{f.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Post Feed */}
      <PostFeed
        author={username}
        title={`Posts by u/${username}`}
      />
    </div>
  )
}
