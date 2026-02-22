import { useEffect, useState, useCallback } from 'react'
import { get, post, del } from '../api/client'
import './Subreddits.css'

const NAME_RE = /^[A-Za-z0-9_]{1,21}$/

function extractApiError(msg) {
  try { return JSON.parse(msg.slice(msg.indexOf('{'))).detail } catch { return msg }
}

function formatTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export default function Subreddits() {
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

  useEffect(() => {
    fetchSubreddits()
  }, [fetchSubreddits])

  const handleAdd = async (e) => {
    e.preventDefault()
    const name = newName.trim()
    if (!NAME_RE.test(name)) {
      setAddError('Must be 1–21 alphanumeric/underscore characters')
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

  const totalPosts = subreddits.reduce((s, r) => s + r.post_count, 0)
  const mostActive = subreddits.length
    ? [...subreddits].sort((a, b) => b.post_count - a.post_count)[0]
    : null

  const nameValid = newName.trim() === '' || NAME_RE.test(newName.trim())

  return (
    <div className="subreddits-page">
      <h1>Subreddits</h1>

      {/* Stats */}
      <div className="sub-stats-grid">
        <div className="dash-card">
          <h2>Total Subreddits</h2>
          <div className="stats-grid">
            <div className="stat-item">
              <div className="stat-value">{subreddits.length}</div>
              <div className="stat-label">configured</div>
            </div>
          </div>
        </div>
        <div className="dash-card">
          <h2>Total Posts</h2>
          <div className="stats-grid">
            <div className="stat-item">
              <div className="stat-value">{totalPosts.toLocaleString()}</div>
              <div className="stat-label">collected</div>
            </div>
          </div>
        </div>
        <div className="dash-card">
          <h2>Most Active</h2>
          <div className="stats-grid">
            <div className="stat-item">
              <div className="stat-value">{mostActive ? `r/${mostActive.name}` : '—'}</div>
              <div className="stat-label">{mostActive ? `${mostActive.post_count.toLocaleString()} posts` : ''}</div>
            </div>
          </div>
        </div>
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
            <div className="sub-add-error">Must be 1–21 alphanumeric/underscore characters</div>
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
                    <td className="sub-name-cell">r/{sub.name}</td>
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
