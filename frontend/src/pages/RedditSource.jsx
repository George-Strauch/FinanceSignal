import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { get, post, del } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import './RedditSource.css'

const NAME_RE = /^[A-Za-z0-9_]{1,21}$/
const WINDOWS = ['7d', '30d', '90d', 'all']
const AUTHOR_SORTS = [
  { key: 'combined', label: 'Combined' },
  { key: 'post_count', label: 'Posts' },
  { key: 'comment_count', label: 'Comments' },
  { key: 'avg_post_score', label: 'Avg Score' },
]

function extractApiError(msg) {
  try { return JSON.parse(msg.slice(msg.indexOf('{'))).detail } catch { return msg }
}

function formatTime(iso) {
  if (!iso) return '\u2014'
  const d = new Date(iso)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function formatTimestamp(ts) {
  if (!ts) return ''
  if (ts.length <= 10) return ts
  return ts.slice(5, 10) + ' ' + ts.slice(11, 16)
}

function formatNum(n) {
  if (n == null) return '\u2014'
  return Number(n).toLocaleString()
}

export default function RedditSource() {
  const [window, setWindow] = usePersistedState('reddit-source-window', '30d')
  const [subreddits, setSubreddits] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [newName, setNewName] = useState('')
  const [addError, setAddError] = useState(null)
  const [addLoading, setAddLoading] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [sortKey, setSortKey] = useState('name')
  const [sortDir, setSortDir] = useState('asc')

  // Analytics state
  const [overview, setOverview] = useState(null)
  const [overviewLoading, setOverviewLoading] = useState(true)
  const [activityData, setActivityData] = useState(null)
  const [activityLoading, setActivityLoading] = useState(true)
  const [authors, setAuthors] = useState(null)
  const [authorsLoading, setAuthorsLoading] = useState(true)
  const [authorSort, setAuthorSort] = usePersistedState('reddit-author-sort', 'combined')

  const fetchSubreddits = useCallback(async () => {
    try {
      const data = await get('/subreddits')
      setSubreddits(data.subreddits)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchOverview = useCallback(async () => {
    setOverviewLoading(true)
    try {
      const data = await get(`/reddit-stats/overview?window=${window}`)
      setOverview(data)
    } catch { setOverview(null) }
    finally { setOverviewLoading(false) }
  }, [window])

  const fetchActivity = useCallback(async () => {
    setActivityLoading(true)
    try {
      const data = await get(`/reddit-stats/activity?window=${window}`)
      setActivityData(data)
    } catch { setActivityData(null) }
    finally { setActivityLoading(false) }
  }, [window])

  const fetchAuthors = useCallback(async () => {
    setAuthorsLoading(true)
    try {
      const data = await get(`/reddit-stats/top-authors?window=${window}&sort_by=${authorSort}&limit=15`)
      setAuthors(data)
    } catch { setAuthors(null) }
    finally { setAuthorsLoading(false) }
  }, [window, authorSort])

  useEffect(() => { fetchSubreddits() }, [fetchSubreddits])
  useEffect(() => { fetchOverview() }, [fetchOverview])
  useEffect(() => { fetchActivity() }, [fetchActivity])
  useEffect(() => { fetchAuthors() }, [fetchAuthors])

  const handleAdd = async (e) => {
    e.preventDefault()
    const name = newName.trim()
    if (!NAME_RE.test(name)) {
      setAddError('Must be 1\u201321 alphanumeric/underscore characters')
      return
    }
    setAddLoading(true)
    setAddError(null)
    try {
      const data = await post('/subreddits', { name })
      setSubreddits(data.subreddits)
      setNewName('')
    } catch (err) {
      setAddError(extractApiError(err.message))
    } finally {
      setAddLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!confirmDelete) return
    setDeleteLoading(true)
    try {
      const data = await del(`/subreddits/${confirmDelete}`)
      setSubreddits(data.subreddits)
      setConfirmDelete(null)
    } catch (err) {
      setAddError(extractApiError(err.message))
      setConfirmDelete(null)
    } finally {
      setDeleteLoading(false)
    }
  }

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'name' ? 'asc' : 'desc')
    }
  }

  const sortIndicator = (key) => {
    if (sortKey !== key) return ''
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC'
  }

  const sorted = [...subreddits].sort((a, b) => {
    let aVal = a[sortKey]
    let bVal = b[sortKey]
    if (sortKey === 'name') {
      aVal = (aVal || '').toLowerCase()
      bVal = (bVal || '').toLowerCase()
      return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
    }
    if (sortKey === 'last_fetched_at') {
      aVal = aVal ? new Date(aVal).getTime() : 0
      bVal = bVal ? new Date(bVal).getTime() : 0
    }
    return sortDir === 'asc' ? (aVal || 0) - (bVal || 0) : (bVal || 0) - (aVal || 0)
  })

  const nameValid = newName.trim() === '' || NAME_RE.test(newName.trim())

  return (
    <div className="reddit-source">
      <nav className="breadcrumb">
        <Link to="/sources" className="breadcrumb-link">Sources</Link>
        <span className="breadcrumb-sep">/</span>
        <span className="breadcrumb-current">Reddit</span>
      </nav>

      <div className="rs-header">
        <h1>Reddit Analytics</h1>
        <div className="rs-window-selector">
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

      {/* Stat Cards */}
      <div className="rs-stats-grid">
        {overviewLoading && !overview ? (
          [...Array(6)].map((_, i) => (
            <div key={i} className="rs-stat-card">
              <div className="skel-line" style={{ height: 28, width: '60%', margin: '0 auto 6px' }} />
              <div className="skel-line" style={{ height: 12, width: '50%', margin: '0 auto' }} />
            </div>
          ))
        ) : overview && (
          <>
            <div className="rs-stat-card">
              <div className="rs-stat-value">{formatNum(overview.total_posts)}</div>
              <div className="rs-stat-label">Total Posts</div>
            </div>
            <div className="rs-stat-card">
              <div className="rs-stat-value">{formatNum(overview.total_comments)}</div>
              <div className="rs-stat-label">Total Comments</div>
            </div>
            <div className="rs-stat-card">
              <div className="rs-stat-value">{formatNum(overview.unique_post_authors)}</div>
              <div className="rs-stat-label">Unique Authors</div>
            </div>
            <div className="rs-stat-card">
              <div className="rs-stat-value">{subreddits.length}</div>
              <div className="rs-stat-label">Subreddits</div>
            </div>
            <div className="rs-stat-card">
              <div className="rs-stat-value">{overview.avg_posts_per_day}</div>
              <div className="rs-stat-label">Avg Posts/Day</div>
            </div>
            <div className="rs-stat-card">
              <div className="rs-stat-value">{overview.avg_score}</div>
              <div className="rs-stat-label">Avg Score</div>
            </div>
          </>
        )}
      </div>

      {/* Activity Chart */}
      <div className="rs-chart-section">
        <h2>Activity Over Time</h2>
        {activityLoading && !activityData && (
          <div className="rs-chart-skeleton">
            <div className="skel-line skel-chart-area" />
          </div>
        )}
        {activityData && activityData.timeline.length === 0 && (
          <p className="rs-no-data">No activity data for this window.</p>
        )}
        {activityData && activityData.timeline.length > 0 && (
          <div className="rs-chart">
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
      <div className="rs-authors-section">
        <div className="rs-authors-header">
          <h2>Top Contributors</h2>
          <div className="rs-author-sort-tabs">
            {AUTHOR_SORTS.map((s) => (
              <button
                key={s.key}
                className={`rs-sort-tab ${authorSort === s.key ? 'active' : ''}`}
                onClick={() => setAuthorSort(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
        {authorsLoading && !authors && (
          <div className="rs-authors-skeleton">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="skel-line" style={{ height: 36, marginBottom: 6 }} />
            ))}
          </div>
        )}
        {authors && authors.authors.length === 0 && (
          <p className="rs-no-data">No author data for this window.</p>
        )}
        {authors && authors.authors.length > 0 && (
          <div className="rs-authors-table-wrap">
            <table className="rs-authors-table">
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
                    <td className="rs-rank">{i + 1}</td>
                    <td className="rs-author-name">
                      <Link to={`/authors/${a.author}`}>u/{a.author}</Link>
                    </td>
                    <td>{a.post_count.toLocaleString()}</td>
                    <td>{a.comment_count.toLocaleString()}</td>
                    <td className="rs-combined">{a.combined.toLocaleString()}</td>
                    <td>{a.avg_post_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add Form */}
      <div className="dash-card">
        <h2>Add Subreddit</h2>
        <form className="sub-add-form" onSubmit={handleAdd}>
          <div className="sub-add-row">
            <div className="sub-add-input-wrap">
              <span className="sub-add-prefix">r/</span>
              <input
                type="text"
                className={`sub-add-input${!nameValid ? ' invalid' : ''}`}
                placeholder="subreddit name"
                value={newName}
                onChange={(e) => { setNewName(e.target.value); setAddError(null) }}
                maxLength={21}
              />
            </div>
            <button
              type="submit"
              className="sub-add-btn"
              disabled={addLoading || !newName.trim() || !nameValid}
            >
              {addLoading ? 'Adding\u2026' : 'Add'}
            </button>
          </div>
          {!nameValid && (
            <div className="sub-add-error">Must be 1\u201321 alphanumeric/underscore characters</div>
          )}
          {addError && <div className="sub-add-error">{addError}</div>}
        </form>
      </div>

      {/* Error */}
      {error && <div className="sub-error">Failed to load: {error}</div>}

      {/* Loading */}
      {loading && <p className="dash-placeholder">Loading\u2026</p>}

      {/* Table */}
      {!loading && subreddits.length > 0 && (
        <div className="dash-card">
          <h2>Configured Subreddits</h2>
          <div className="sub-table-wrap">
            <table className="sub-table">
              <thead>
                <tr>
                  <th className="sortable" onClick={() => handleSort('name')}>
                    Name{sortIndicator('name')}
                  </th>
                  <th className="sortable" onClick={() => handleSort('post_count')}>
                    Posts{sortIndicator('post_count')}
                  </th>
                  <th className="sortable" onClick={() => handleSort('last_fetched_at')}>
                    Last Fetched{sortIndicator('last_fetched_at')}
                  </th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((sub) => (
                  <tr key={sub.name}>
                    <td className="sub-name-cell">
                      <Link to={`/sources/reddit/${sub.name}`} className="sub-name-link">
                        r/{sub.name}
                      </Link>
                    </td>
                    <td>{sub.post_count.toLocaleString()}</td>
                    <td title={sub.last_fetched_at || ''}>{formatTime(sub.last_fetched_at)}</td>
                    <td>
                      {sub.is_active
                        ? <span className="sub-status-active">Active</span>
                        : <span className="sub-status-inactive">Inactive</span>
                      }
                    </td>
                    <td>
                      <button
                        className="sub-remove-btn"
                        onClick={() => setConfirmDelete(sub.name)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Confirm Delete Overlay */}
      {confirmDelete && (
        <div className="sub-confirm-overlay" onClick={() => !deleteLoading && setConfirmDelete(null)}>
          <div className="sub-confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Remove r/{confirmDelete}?</h3>
            <p>This will stop future collection but won't delete existing data.</p>
            <div className="sub-confirm-actions">
              <button
                className="sub-confirm-cancel"
                onClick={() => setConfirmDelete(null)}
                disabled={deleteLoading}
              >
                Cancel
              </button>
              <button
                className="sub-confirm-delete"
                onClick={handleDelete}
                disabled={deleteLoading}
              >
                {deleteLoading ? 'Removing\u2026' : 'Remove'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
