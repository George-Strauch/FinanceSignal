import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiSearch, FiUsers, FiTag } from 'react-icons/fi'
import { get } from '../api/client'
import usePersistedState from '../hooks/usePersistedState'
import './Entities.css'

const LABEL_FILTERS = [
  { value: 'all', display: 'All' },
  { value: 'PERSON', display: 'People' },
  { value: 'ORG', display: 'Companies' },
  { value: 'GPE', display: 'Places' },
  { value: 'PRODUCT', display: 'Products' },
  { value: 'EVENT', display: 'Events' },
  { value: 'NORP', display: 'Groups' },
  { value: 'FAC', display: 'Facilities' },
  { value: 'WORK_OF_ART', display: 'Works' },
  { value: 'LAW', display: 'Laws' },
  { value: 'MISC', display: 'Misc' },
]

export default function Entities() {
  const navigate = useNavigate()
  const [label, setLabel] = usePersistedState('entities-label', 'all')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [stats, setStats] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const labelParam = label !== 'all' ? `&label=${label}` : ''
      const res = await get(`/entities/canonical?limit=500${labelParam}`)
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

  const allEntities = data?.entities || []

  const filteredEntities = searchQuery
    ? allEntities.filter(e => {
        const q = searchQuery.toLowerCase()
        return e.canonical_text?.toLowerCase().includes(q) ||
               e.aliases?.some(a => a.alias_text?.toLowerCase().includes(q))
      })
    : allEntities

  const sortedEntities = [...filteredEntities].sort((a, b) => {
    return (b.article_count || 0) - (a.article_count || 0)
  })

  const isEmpty = !loading && sortedEntities.length === 0

  return (
    <div className="entities-page">
      <div className="entities-header">
        <div className="entities-title-row">
          <h1>Canonical Entities</h1>
        </div>
      </div>

      {stats && (
        <div className="entities-stats-bar">
          <div className="entities-stat">
            <span className="entities-stat-value">{data?.total?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Canonical Entities</span>
          </div>
          <div className="entities-stat">
            <span className="entities-stat-value">{stats.unique_entities?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Unique Extractions</span>
          </div>
          <div className="entities-stat">
            <span className="entities-stat-value">{stats.total_entity_mentions?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Total Mentions</span>
          </div>
          <div className="entities-stat">
            <span className="entities-stat-value">{stats.posts_processed?.toLocaleString() ?? '-'}</span>
            <span className="entities-stat-label">Posts Processed</span>
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
          <div className="entities-search-wrap">
            <FiSearch className="entities-search-icon" />
            <input
              type="text"
              className="entities-search-input"
              placeholder="Search canonical entities or aliases..."
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
          <p>No canonical entities found.</p>
          <p className="entities-empty-hint">Run the Entity Mass-Correct process to canonicalize extracted entities.</p>
        </div>
      )}

      {!loading && !isEmpty && (
        <div className="entities-table-wrap">
          <table className="entities-table">
            <thead>
              <tr>
                <th>Canonical Entity</th>
                <th>Type</th>
                <th>Articles</th>
                <th>Aliases</th>
                <th>Ticker</th>
              </tr>
            </thead>
            <tbody>
              {sortedEntities.map((e) => (
                <tr
                  key={e.id}
                  className="clickable-row"
                  onClick={() => navigate(`/entities/${e.id}`)}
                >
                  <td className="entity-text-cell">{e.canonical_text}</td>
                  <td>
                    <span className={`entity-label-badge label-${e.canonical_label}`}>
                      {e.label_display || e.canonical_label}
                    </span>
                  </td>
                  <td>{(e.article_count ?? 0).toLocaleString()}</td>
                  <td>{e.alias_count ?? 0}</td>
                  <td>
                    {e.ticker_link ? (
                      <span
                        className="entity-ticker-chip"
                        onClick={(ev) => { ev.stopPropagation(); navigate(`/tickers/${e.ticker_link}`) }}
                      >
                        <FiTag /> {e.ticker_link}
                      </span>
                    ) : '—'}
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
