import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiSearch, FiUsers } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import './Entities.css'

const WINDOWS = ['1d', '7d', '30d', '90d']

const LABEL_FILTERS = [
  { value: 'all', display: 'All' },
  { value: 'PERSON', display: 'People' },
  { value: 'ORG', display: 'Companies' },
  { value: 'GPE', display: 'Places' },
  { value: 'MONEY', display: 'Money' },
  { value: 'PRODUCT', display: 'Products' },
  { value: 'EVENT', display: 'Events' },
  { value: 'NORP', display: 'Groups' },
]

export default function Entities() {
  const navigate = useNavigate()
  const [window, setWindow] = usePersistedState('entities-window', '7d')
  const [label, setLabel] = usePersistedState('entities-label', 'all')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [stats, setStats] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [sortKey, setSortKey] = useState('mention_count')
  const [sortDir, setSortDir] = useState('desc')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const labelParam = label !== 'all' ? `&label=${label}` : ''
      const res = await get(`/entities/top?window=${window}&limit=100${labelParam}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [window, label])

  useEffect(() => { fetchData() }, [fetchData])

  useEffect(() => {
    get('/entities/stats').then(setStats).catch(() => {})
  }, [])

  useEffect(() => {
    if (!searchQuery || searchQuery.length < 2) {
      setSearchResults(null)
      return
    }
    const timeout = setTimeout(async () => {
      try {
        const labelParam = label !== 'all' ? `&label=${label}` : ''
        const res = await get(`/entities/search?q=${encodeURIComponent(searchQuery)}${labelParam}&limit=20`)
        setSearchResults(res.results)
      } catch {
        setSearchResults(null)
      }
    }, 300)
    return () => clearTimeout(timeout)
  }, [searchQuery, label])

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sortIndicator = (key) => {
    if (sortKey !== key) return ''
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC'
  }

  const entities = searchResults || data?.entities || []
  const sortedEntities = [...entities].sort((a, b) => {
    const aVal = a[sortKey]
    const bVal = b[sortKey]
    if (typeof aVal === 'string') {
      return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
    }
    return sortDir === 'asc' ? aVal - bVal : bVal - aVal
  })

  const isEmpty = !loading && entities.length === 0

  return (
    <div className="entities-page">
      <div className="entities-header">
        <div className="entities-title-row">
          <h1>Named Entities</h1>
        </div>
        <div className="entities-controls">
          <div className="window-selector">
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
      </div>

      {stats && (
        <div className="entities-stats-bar">
          <div className="entities-stat">
            <span className="entities-stat-value">{stats.unique_entities?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Unique Entities</span>
          </div>
          <div className="entities-stat">
            <span className="entities-stat-value">{stats.total_entity_mentions?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Total Mentions</span>
          </div>
          <div className="entities-stat">
            <span className="entities-stat-value">{stats.posts_processed?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Posts Processed</span>
          </div>
          <div className="entities-stat">
            <span className="entities-stat-value">{stats.comments_processed?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Comments Processed</span>
          </div>
        </div>
      )}

      <div className="entities-filters">
        <div className="entities-label-chips">
          {LABEL_FILTERS.map((f) => (
            <button
              key={f.value}
              className={`entities-label-chip ${label === f.value ? 'active' : ''}`}
              onClick={() => setLabel(f.value)}
            >
              {f.display}
            </button>
          ))}
        </div>
        <div className="entities-search-wrap">
          <FiSearch className="entities-search-icon" />
          <input
            type="text"
            className="entities-search-input"
            placeholder="Search entities..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {error && <div className="entities-error">Failed to load: {error}</div>}

      {loading && !data && (
        <div className="entities-skeleton">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="entities-skel-row">
              <div className="skel-line" style={{ width: '30%', height: 16 }} />
              <div className="skel-line" style={{ width: '15%', height: 16 }} />
              <div className="skel-line" style={{ width: '10%', height: 16 }} />
            </div>
          ))}
        </div>
      )}

      {isEmpty && (
        <div className="entities-empty">
          <FiUsers className="entities-empty-icon" />
          <p>No entities found for this window.</p>
          <p className="entities-empty-hint">Run the NER Extraction process first, or try a wider time window.</p>
        </div>
      )}

      {!loading && !isEmpty && (
        <div className="entities-table-wrap">
          <table className="entities-table">
            <thead>
              <tr>
                <th>#</th>
                <th className="sortable" onClick={() => handleSort('entity_text')}>
                  Entity{sortIndicator('entity_text')}
                </th>
                <th>Type</th>
                <th className="sortable" onClick={() => handleSort('mention_count')}>
                  Mentions{sortIndicator('mention_count')}
                </th>
                <th>Subreddits</th>
              </tr>
            </thead>
            <tbody>
              {sortedEntities.map((e, i) => (
                <tr
                  key={`${e.entity_text}-${e.entity_label}`}
                  className="clickable-row"
                  onClick={() => navigate(`/entities/${encodeURIComponent(e.entity_text)}`)}
                >
                  <td className="rank-cell">{i + 1}</td>
                  <td className="entity-text-cell">{e.entity_text}</td>
                  <td>
                    <span className={`entity-label-badge label-${e.entity_label}`}>
                      {e.label_display || e.entity_label}
                    </span>
                  </td>
                  <td>{e.mention_count.toLocaleString()}</td>
                  <td>
                    {e.subreddits && typeof e.subreddits === 'object' && !Array.isArray(e.subreddits) ? (
                      <div className="entity-sub-chips">
                        {Object.entries(e.subreddits).slice(0, 3).map(([sub, cnt]) => (
                          <span key={sub} className="entity-sub-chip">r/{sub} ({cnt})</span>
                        ))}
                        {Object.keys(e.subreddits).length > 3 && (
                          <span className="entity-sub-chip">+{Object.keys(e.subreddits).length - 3}</span>
                        )}
                      </div>
                    ) : (
                      <span className="entity-sub-count">{e.subreddit_count ?? '-'}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
