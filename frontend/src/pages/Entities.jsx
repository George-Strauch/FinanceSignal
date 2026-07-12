import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiSearch, FiUsers } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import './Entities.css'

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

const SORT_OPTIONS = [
  { value: 'mention_count', label: 'Occurrences' },
  { value: 'last_seen', label: 'Last Seen' },
  { value: 'entity_text', label: 'Alphabetical' },
]

function formatRelative(unixTs) {
  if (!unixTs) return '—'
  const diff = Math.floor((Date.now() / 1000) - unixTs)
  if (diff < 0) return 'just now'
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 2592000) return `${Math.floor(diff / 86400)}d ago`
  return `${Math.floor(diff / 2592000)}mo ago`
}

export default function Entities() {
  const navigate = useNavigate()
  const [label, setLabel] = usePersistedState('entities-label', 'all')
  const [sortKey, setSortKey] = usePersistedState('entities-sort', 'mention_count')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [stats, setStats] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const labelParam = label !== 'all' ? `&label=${label}` : ''
      const res = await get(`/entities/top?limit=200${labelParam}`)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [label])

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

  const entities = searchResults || data?.entities || []
  const sortedEntities = [...entities].sort((a, b) => {
    const dir = sortKey === 'entity_text' ? 1 : -1
    if (sortKey === 'entity_text') {
      return a.entity_text.localeCompare(b.entity_text)
    }
    return (a[sortKey] ?? 0) - (b[sortKey] ?? 0) > 0 ? dir : -dir
  })

  const isEmpty = !loading && entities.length === 0

  return (
    <div className="entities-page">
      <div className="entities-header">
        <div className="entities-title-row">
          <h1>Named Entities</h1>
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
        <div className="entities-controls-right">
          <div className="entities-sort-wrap">
            <span className="entities-sort-label">Sort</span>
            <select
              className="entities-sort-select"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value)}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
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
          <p>No entities found.</p>
          <p className="entities-empty-hint">Run the NER Extraction process to populate entity data.</p>
        </div>
      )}

      {!loading && !isEmpty && (
        <div className="entities-table-wrap">
          <table className="entities-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Entity</th>
                <th>Type</th>
                <th>Occurrences</th>
                <th>Last Seen</th>
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
                  <td>{(e.mention_count ?? 0).toLocaleString()}</td>
                  <td className="entity-last-seen-cell">{formatRelative(e.last_seen)}</td>
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