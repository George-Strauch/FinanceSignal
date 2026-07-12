import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiBell, FiChevronDown, FiChevronUp, FiExternalLink, FiClock, FiCheck, FiX, FiRotateCcw } from 'react-icons/fi'
import ReactMarkdown from 'react-markdown'
import { get, post, del } from '../api/client'
import './Events.css'

const STATUS_FILTERS = [
  { value: 'active', label: 'Active' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: 'all', label: 'All' },
]

function formatRelativeTime(epoch) {
  if (!epoch) return ''
  const diff = Date.now() / 1000 - epoch
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function formatCountdown(ts) {
  if (!ts) return 'TBD'
  const diff = ts - Date.now() / 1000
  if (diff < 0) return `${Math.floor(Math.abs(diff) / 86400)}d overdue`
  if (diff < 86400) return `in ${Math.floor(diff / 3600)}h`
  return `in ${Math.floor(diff / 86400)}d`
}

function EventCard({ event, onAction, onNavigate }) {
  const [expanded, setExpanded] = useState(false)
  const [sourcesExpanded, setSourcesExpanded] = useState(false)
  const [changeLogExpanded, setChangeLogExpanded] = useState(false)
  const [resolveInput, setResolveInput] = useState('')
  const [dismissInput, setDismissInput] = useState('')
  const [showResolve, setShowResolve] = useState(false)
  const [showDismiss, setShowDismiss] = useState(false)

  const statusClass = event.status === 'active'
    ? (event.stale ? 'stale' : 'active')
    : event.status

  return (
    <div className={`event-card ${statusClass}`}>
      <div className="event-card-header">
        <div className="event-summary">{event.summary}</div>
        <span className={`event-status-badge ${event.status}`}>
          {event.status === 'discovered_and_resolved' ? 'resolved' : event.status}
          {event.stale && ' · overdue'}
        </span>
      </div>

      <div className="event-tickers">
        {event.related_tickers.map((t) => (
          <span
            key={t}
            className="event-ticker-chip"
            onClick={() => onNavigate(`/tickers/${t}`)}
          >
            {t}
          </span>
        ))}
      </div>

      <div className="event-context">
        <ReactMarkdown>{expanded ? event.context : event.context.split('\n')[0]}</ReactMarkdown>
        {event.context.includes('\n') && (
          <button className="event-expand-btn" onClick={() => setExpanded(!expanded)}>
            {expanded ? <><FiChevronUp /> Less</> : <><FiChevronDown /> More</>}
          </button>
        )}
      </div>

      {event.expected_updates && event.expected_updates.length > 0 && (
        <div className="event-updates">
          {event.expected_updates.map((u, i) => {
            const ts = u.timestamp ? parseFloat(u.timestamp) : null
            const overdue = ts && ts < Date.now() / 1000 && u.type === 'resolution' && event.status === 'active'
            return (
              <div key={i} className={`event-update-item ${overdue ? 'overdue' : ''}`}>
                <span className="event-update-type">{u.type}</span>
                <span className="event-update-label">{u.label}</span>
                <span className="event-update-countdown">{ts ? formatCountdown(ts) : 'TBD'}</span>
                {event.status === 'active' && (
                  <button
                    className="event-update-delete"
                    onClick={() => onAction('delete-update', { id: event.id, index: i })}
                    title="Remove this update"
                  >
                    <FiX />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="event-meta">
        <span className="event-meta-item">
          <FiClock /> {formatRelativeTime(event.discovered_at)}
        </span>
        <button
          className="event-sources-btn"
          onClick={() => setSourcesExpanded(!sourcesExpanded)}
        >
          <FiExternalLink /> {event.sources?.length || 0} sources
        </button>
        {event.created_by_analysis && (
          <span className="event-meta-item">Analysis #{event.created_by_analysis}</span>
        )}
      </div>

      {sourcesExpanded && event.sources && event.sources.length > 0 && (
        <div className="event-sources-list">
          {event.sources.map((s, i) => (
            <div key={i} className="event-source-item">
              <span className={`event-source-type ${s.source_type}`}>{s.source_type}</span>
              {s.post && (
                <>
                  <span className="event-source-title">{s.post.title}</span>
                  <span className="event-source-meta">r/{s.post.subreddit} · u/{s.post.author} · {s.post.score} pts</span>
                </>
              )}
              {s.comment && (
                <>
                  <span className="event-source-title">{s.comment.post_title}</span>
                  <span className="event-source-meta">r/{s.comment.subreddit} · u/{s.comment.author} · {s.comment.score} pts</span>
                </>
              )}
              {s.reddit_url && (
                <a href={s.reddit_url} target="_blank" rel="noopener noreferrer" className="event-source-link">
                  <FiExternalLink />
                </a>
              )}
            </div>
          ))}
        </div>
      )}

      {event.resolution_notes && event.status !== 'active' && (
        <div className="event-resolution-notes">
          <strong>Resolution:</strong> {event.resolution_notes}
        </div>
      )}

      <div className="event-actions">
        {event.status === 'active' && !showResolve && !showDismiss && (
          <>
            <button className="event-action-btn resolve" onClick={() => setShowResolve(true)}>
              <FiCheck /> Resolve
            </button>
            <button className="event-action-btn dismiss" onClick={() => setShowDismiss(true)}>
              <FiX /> Dismiss
            </button>
          </>
        )}
        {event.status !== 'active' && !showResolve && !showDismiss && (
          <button className="event-action-btn reactivate" onClick={() => onAction('reactivate', { id: event.id })}>
            <FiRotateCcw /> Reactivate
          </button>
        )}
      </div>

      {showResolve && (
        <div className="event-input-row">
          <input
            type="text"
            placeholder="Resolution notes..."
            value={resolveInput}
            onChange={(e) => setResolveInput(e.target.value)}
            className="event-input"
          />
          <button
            className="event-action-btn resolve"
            disabled={!resolveInput}
            onClick={() => {
              onAction('resolve', { id: event.id, notes: resolveInput })
              setShowResolve(false)
              setResolveInput('')
            }}
          >
            Confirm
          </button>
          <button className="event-action-btn cancel" onClick={() => setShowResolve(false)}>Cancel</button>
        </div>
      )}

      {showDismiss && (
        <div className="event-input-row">
          <input
            type="text"
            placeholder="Why dismiss?"
            value={dismissInput}
            onChange={(e) => setDismissInput(e.target.value)}
            className="event-input"
          />
          <button
            className="event-action-btn dismiss"
            onClick={() => {
              onAction('dismiss', { id: event.id, notes: dismissInput })
              setShowDismiss(false)
              setDismissInput('')
            }}
          >
            Confirm
          </button>
          <button className="event-action-btn cancel" onClick={() => setShowDismiss(false)}>Cancel</button>
        </div>
      )}

      <button
        className="event-changelog-toggle"
        onClick={() => setChangeLogExpanded(!changeLogExpanded)}
      >
        {changeLogExpanded ? <><FiChevronUp /> Hide change log</> : <><FiChevronDown /> Change log ({event.change_log?.length || 0})</>}
      </button>

      {changeLogExpanded && event.change_log && event.change_log.length > 0 && (
        <div className="event-changelog">
          {event.change_log.map((c, i) => (
            <div key={i} className="event-changelog-entry">
              <span className="event-changelog-action">{c.action}</span>
              <span className="event-changelog-source">{c.source}</span>
              {c.detail && <span className="event-changelog-detail">{c.detail}</span>}
              <span className="event-changelog-time">{formatRelativeTime(c.ts)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Events() {
  const navigate = useNavigate()
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('active')
  const [tickerFilter, setTickerFilter] = useState('')
  const [sortBy, setSortBy] = useState('discovered')

  const fetchEvents = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ status: statusFilter, sort: sortBy })
      if (tickerFilter) params.set('ticker', tickerFilter)
      const res = await get(`/events?${params}`)
      setEvents(res.events)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, tickerFilter, sortBy])

  useEffect(() => { fetchEvents() }, [fetchEvents])

  const handleAction = useCallback(async (action, payload) => {
    try {
      if (action === 'resolve') {
        await post(`/events/${payload.id}/resolve`, { resolution_notes: payload.notes })
      } else if (action === 'dismiss') {
        await post(`/events/${payload.id}/dismiss`, { notes: payload.notes })
      } else if (action === 'reactivate') {
        await post(`/events/${payload.id}/reactivate`)
      } else if (action === 'delete-update') {
        await del(`/events/${payload.id}/expected-updates/${payload.index}`)
      }
      fetchEvents()
    } catch (err) {
      setError(err.message)
    }
  }, [fetchEvents])

  return (
    <div className="events-page">
      <div className="events-header">
        <h1><FiBell /> Event Watcher</h1>
        <div className="events-filters">
          <div className="events-status-filters">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                className={`events-filter-btn ${statusFilter === f.value ? 'active' : ''}`}
                onClick={() => setStatusFilter(f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <input
            type="text"
            placeholder="Filter by ticker..."
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
            className="events-ticker-filter"
          />
          <select
            className="events-sort-select"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
          >
            <option value="discovered">Sort: Discovered</option>
            <option value="updated">Sort: Updated</option>
          </select>
        </div>
      </div>

      {error && <div className="events-error">{error}</div>}

      {loading ? (
        <div className="events-loading">Loading events...</div>
      ) : events.length === 0 ? (
        <div className="events-empty">No events found. Run an LLM analysis with tools enabled to discover events.</div>
      ) : (
        <div className="events-list">
          {events.map((event) => (
            <EventCard
              key={event.id}
              event={event}
              onAction={handleAction}
              onNavigate={navigate}
            />
          ))}
        </div>
      )}
    </div>
  )
}