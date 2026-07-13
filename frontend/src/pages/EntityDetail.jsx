import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { FiArrowLeft, FiTag, FiClock, FiEdit } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import PostFeed from '../components/PostFeed'
import './EntityDetail.css'

const WINDOWS = ['1d', '7d', '30d', '90d']

const PALETTE = [
  '99, 102, 241', '244, 114, 182', '52, 211, 153', '251, 191, 36',
  '96, 165, 250', '167, 139, 250', '248, 113, 113', '45, 212, 191',
  '253, 186, 116', '156, 163, 175',
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

function formatEpoch(epoch) {
  if (!epoch) return '—'
  return new Date(epoch * 1000).toLocaleDateString()
}

export default function EntityDetail() {
  const { entityId } = useParams()
  const navigate = useNavigate()
  const [window, setWindow] = usePersistedState('entity-detail-window', '7d')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await get(`/entities/canonical/${entityId}?window=${window}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [entityId, window])

  useEffect(() => { fetchDetail() }, [fetchDetail])

  const subreddits = data ? Object.keys(data.mentions_by_subreddit) : []
  const chartData = data ? pivotChartData(data.mentions_over_time, subreddits) : []
  const topSubreddit = subreddits.length > 0 ? subreddits[0] : '—'

  return (
    <div className="entity-detail">
      <div className="ed-header">
        <button className="ed-back" onClick={() => navigate(-1)}>
          <FiArrowLeft />
        </button>
        <div className="ed-title-group">
          <div className="ed-title-row">
            <h1 className="ed-entity-name">{data?.canonical_text || 'Loading…'}</h1>
            {data && (
              <span className={`entity-label-badge label-${data.canonical_label}`}>
                {data.label_display}
              </span>
            )}
          </div>
          {data && (
            <span className="ed-mention-count">
              {data.total_mentions.toLocaleString()} mentions
            </span>
          )}
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

      {/* Description + metadata */}
      {data?.description && (
        <div className="dash-card">
          <h2>Description</h2>
          <p className="entity-description-text">{data.description}</p>
          <div className="entity-meta-grid">
            <div className="detail-field">
              <span className="detail-field-label">Source</span>
              <span className="detail-field-value">{data.source || '—'}</span>
            </div>
            <div className="detail-field">
              <span className="detail-field-label">Created</span>
              <span className="detail-field-value">{formatEpoch(data.created_at)}</span>
            </div>
            <div className="detail-field">
              <span className="detail-field-label">Updated</span>
              <span className="detail-field-value">{formatEpoch(data.updated_at)}</span>
            </div>
            {data.ticker_link && (
              <div className="detail-field">
                <span className="detail-field-label">Ticker</span>
                <span
                  className="entity-ticker-chip clickable"
                  onClick={() => navigate(`/tickers/${data.ticker_link}`)}
                >
                  <FiTag /> {data.ticker_link}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Ticker tags */}
      {data?.ticker_tags?.length > 0 && (
        <div className="dash-card">
          <h2>Ticker Tags</h2>
          <div className="ticker-tag-chips">
            {data.ticker_tags.map((t) => (
              <span key={t.id} className="ticker-tag-chip" style={{ borderColor: t.color }}>
                {t.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Stats Cards */}
      {data && (
        <div className="ed-stats">
          <div className="ed-stat-card">
            <div className="ed-stat-value">{data.total_mentions.toLocaleString()}</div>
            <div className="ed-stat-label">Total Mentions</div>
          </div>
          <div className="ed-stat-card">
            <div className="ed-stat-value">{data.unique_posts?.toLocaleString() ?? '-'}</div>
            <div className="ed-stat-label">Unique Posts</div>
          </div>
          <div className="ed-stat-card">
            <div className="ed-stat-value">{topSubreddit}</div>
            <div className="ed-stat-label">Top Subreddit</div>
          </div>
          <div className="ed-stat-card">
            <div className="ed-stat-value">{subreddits.length}</div>
            <div className="ed-stat-label">Subreddits</div>
          </div>
          <div className="ed-stat-card">
            <div className="ed-stat-value">{data.relevance_count ?? 0}</div>
            <div className="ed-stat-label">Relevance Scores</div>
          </div>
          <div className="ed-stat-card">
            <div className="ed-stat-value">{data.aliases?.length ?? 0}</div>
            <div className="ed-stat-label">Aliases</div>
          </div>
        </div>
      )}

      {/* Aliases */}
      {data?.aliases?.length > 0 && (
        <div className="dash-card">
          <h2>Aliases ({data.aliases.length})</h2>
          <div className="alias-chip-list">
            {data.aliases.map((a) => (
              <span key={a.id} className="alias-chip">
                {a.alias_text}
                {a.alias_label && (
                  <span className={`entity-label-badge label-${a.alias_label}`} style={{ marginLeft: 6 }}>
                    {a.alias_label}
                  </span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Mentions Over Time Chart */}
      <div className="ed-chart-section">
        <h2>Mentions Over Time</h2>
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

      {/* Relevance scores */}
      {data?.relevance_scores?.length > 0 && (
        <div className="dash-card">
          <h2>Relevance Scores ({data.relevance_scores.length})</h2>
          <div className="fq-table-wrap">
            <table className="fq-table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Score</th>
                  <th>Model</th>
                </tr>
              </thead>
              <tbody>
                {data.relevance_scores.map((r, i) => (
                  <tr key={i}>
                    <td className="fq-sub">{r.source_type}/{r.source_id.slice(0, 12)}</td>
                    <td>
                      <span className={`relevance-score-badge ${r.score >= 0.5 ? 'high' : 'low'}`}>
                        {r.score.toFixed(3)}
                      </span>
                    </td>
                    <td className="fq-time">{r.model}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Corrections history */}
      {data?.corrections?.length > 0 && (
        <div className="dash-card">
          <h2><FiEdit /> Correction History ({data.corrections.length})</h2>
          <div className="fq-table-wrap">
            <table className="fq-table">
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Tool</th>
                  <th>Pending Text</th>
                  <th>Initiated By</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {data.corrections.map((c) => (
                  <tr key={c.id}>
                    <td><span className="qqueue-badge">{c.action}</span></td>
                    <td>{c.llm_tool_used || '—'}</td>
                    <td className="fq-sub">{c.pending_text || '—'}</td>
                    <td>{c.initiated_by}</td>
                    <td className="fq-time">{formatEpoch(c.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Related Posts */}
      {data?.related_post_ids?.length > 0 && (
        <PostFeed
          entity={data.canonical_text}
          title="Related Posts"
        />
      )}
    </div>
  )
}
