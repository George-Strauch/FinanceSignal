import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { FiArrowLeft, FiUser } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import PostFeed from '../components/PostFeed'
import './EntityDetail.css'

const WINDOWS = ['1d', '7d', '30d', '90d']
const COUNT_MODES = [
  { value: 'mentions', label: 'Mentions' },
  { value: 'authors', label: 'Authors' },
  { value: 'posts', label: 'Posts' },
]

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
  return ts.slice(5, 10) + ' ' + ts.slice(11, 16)
}

export default function EntityDetail() {
  const { entityText } = useParams()
  const navigate = useNavigate()
  const [window, setWindow] = usePersistedState('entity-detail-window', '7d')
  const [countMode, setCountMode] = usePersistedState('count-mode', 'mentions')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Bottom tab state
  const [bottomTab, setBottomTab] = usePersistedState('entity-bottom-tab', 'posts')
  const [authorData, setAuthorData] = useState(null)
  const [authorLoading, setAuthorLoading] = useState(false)

  const decodedEntity = decodeURIComponent(entityText || '')
  const countModeLabel = COUNT_MODES.find((m) => m.value === countMode)?.label.toLowerCase() || 'mentions'

  const fetchAuthors = useCallback(async () => {
    setAuthorLoading(true)
    try {
      const res = await get(`/entities/${encodeURIComponent(decodedEntity)}/authors?window=${window}`)
      setAuthorData(res)
    } catch {
      setAuthorData(null)
    } finally {
      setAuthorLoading(false)
    }
  }, [decodedEntity, window])

  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await get(`/entities/${encodeURIComponent(decodedEntity)}?window=${window}&count_mode=${countMode}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [decodedEntity, window, countMode])

  useEffect(() => { fetchDetail() }, [fetchDetail])
  useEffect(() => {
    if (bottomTab === 'authors') fetchAuthors()
  }, [bottomTab, fetchAuthors])

  const subreddits = data ? Object.keys(data.mentions_by_subreddit) : []
  const chartData = data ? pivotChartData(data.mentions_over_time, subreddits) : []
  const topSubreddit = subreddits.length > 0 ? subreddits[0] : '-'

  return (
    <div className="entity-detail">
      <div className="ed-header">
        <button className="ed-back" onClick={() => navigate(-1)}>
          <FiArrowLeft />
        </button>
        <div className="ed-title-group">
          <div className="ed-title-row">
            <h1 className="ed-entity-name">{decodedEntity}</h1>
            {data && (
              <span className={`entity-label-badge label-${data.entity_label}`}>
                {data.label_display}
              </span>
            )}
          </div>
          {data && (
            <span className="ed-mention-count">
              {data.total_mentions.toLocaleString()} {countModeLabel}
            </span>
          )}
        </div>
        <div className="ed-count-mode-selector">
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
        <div className="ed-window-selector">
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

      {error && <div className="ed-error">Failed to load: {error}</div>}

      {/* Stats Cards */}
      {data && (
        <div className="ed-stats">
          <div className="ed-stat-card">
            <div className="ed-stat-value">{data.total_mentions.toLocaleString()}</div>
            <div className="ed-stat-label">Total {COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions'}</div>
          </div>
          {countMode !== 'posts' && (
            <div className="ed-stat-card">
              <div className="ed-stat-value">{data.unique_posts?.toLocaleString() ?? '-'}</div>
              <div className="ed-stat-label">Unique Posts</div>
            </div>
          )}
          <div className="ed-stat-card">
            <div className="ed-stat-value">{topSubreddit}</div>
            <div className="ed-stat-label">Top Subreddit</div>
          </div>
          <div className="ed-stat-card">
            <div className="ed-stat-value">{subreddits.length}</div>
            <div className="ed-stat-label">Subreddits</div>
          </div>
        </div>
      )}

      {/* Mentions Over Time Chart */}
      <div className="ed-chart-section">
        <h2>{COUNT_MODES.find((m) => m.value === countMode)?.label || 'Mentions'} Over Time</h2>
        {loading && !data && (
          <div className="ed-chart-skeleton">
            <div className="skel-line skel-chart-area" />
          </div>
        )}
        {data && chartData.length === 0 && (
          <p className="ed-no-data">No data for this window.</p>
        )}
        {data && chartData.length > 0 && (
          <div className="ed-chart">
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
                <Legend wrapperStyle={{ fontSize: '0.8rem' }} />
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
        <div className="ed-sub-breakdown">
          <h2>By Subreddit</h2>
          <div className="ed-sub-bars">
            {subreddits.map((sub, i) => {
              const count = data.mentions_by_subreddit[sub]
              const pct = data.total_mentions > 0 ? (count / data.total_mentions) * 100 : 0
              return (
                <div key={sub} className="ed-sub-row">
                  <span className="ed-sub-name">r/{sub}</span>
                  <div className="ed-sub-bar-track">
                    <div
                      className="ed-sub-bar-fill"
                      style={{
                        width: `${pct}%`,
                        background: `rgb(${PALETTE[i % PALETTE.length]})`,
                      }}
                    />
                  </div>
                  <span className="ed-sub-count">{count}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Co-occurring Entities */}
      {data && data.co_occurring_entities?.length > 0 && (
        <div className="ed-cooccur-section">
          <h2>Co-occurring Entities</h2>
          <div className="ed-cooccur-list">
            {data.co_occurring_entities.map((e) => (
              <div
                key={`${e.entity_text}-${e.entity_label}`}
                className="ed-cooccur-item"
                onClick={() => navigate(`/entities/${encodeURIComponent(e.entity_text)}`)}
                role="button"
                tabIndex={0}
                onKeyDown={(ev) => ev.key === 'Enter' && navigate(`/entities/${encodeURIComponent(e.entity_text)}`)}
              >
                <span className="ed-cooccur-text">{e.entity_text}</span>
                <span className={`entity-label-badge label-${e.entity_label}`}>
                  {e.label_display}
                </span>
                <span className="ed-cooccur-count">{e.co_occurrence_count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Bottom Tabs: Posts / By Author */}
      <div className="ed-bottom-tabs">
        <button
          className={`ed-tab-btn ${bottomTab === 'posts' ? 'active' : ''}`}
          onClick={() => setBottomTab('posts')}
        >
          Related Posts
        </button>
        <button
          className={`ed-tab-btn ${bottomTab === 'authors' ? 'active' : ''}`}
          onClick={() => setBottomTab('authors')}
        >
          <FiUser /> By Author
        </button>
      </div>

      {bottomTab === 'posts' && (
        <PostFeed
          entity={decodedEntity}
          title="Related Posts"
        />
      )}

      {bottomTab === 'authors' && (
        <div className="ed-author-section">
          {authorLoading && !authorData && (
            <div className="ed-author-skeleton">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="ed-author-row skeleton">
                  <div className="skel-line" style={{ height: 18, width: '40%' }} />
                  <div className="skel-line" style={{ height: 14, width: '20%' }} />
                </div>
              ))}
            </div>
          )}
          {!authorLoading && (!authorData?.authors || authorData.authors.length === 0) && (
            <p className="ed-no-data">No author data for this window.</p>
          )}
          {authorData?.authors?.length > 0 && (
            <div className="ed-author-list">
              {authorData.authors.map((a, i) => {
                const maxCount = authorData.authors[0].total_count
                const pct = maxCount > 0 ? (a.total_count / maxCount) * 100 : 0
                return (
                  <div
                    key={a.author}
                    className="ed-author-row"
                    onClick={() => navigate(`/authors/${encodeURIComponent(a.author)}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && navigate(`/authors/${encodeURIComponent(a.author)}`)}
                  >
                    <span className="ed-author-rank">{i + 1}</span>
                    <span className="ed-author-name">u/{a.author}</span>
                    <div className="ed-author-bar-track">
                      <div className="ed-author-bar-fill" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="ed-author-counts">
                      {a.post_count}p / {a.comment_count}c
                    </span>
                    <span className="ed-author-total">{a.total_count}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
