import { useEffect, useState, useCallback, useRef } from 'react'
import { FiPlay, FiSquare, FiRefreshCw } from 'react-icons/fi'
import { get, post } from '../api/client'
import './ProcessMonitor.css'

function formatUptime(seconds) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Math.floor(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
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

function formatLogTs(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString()
}

export default function ProcessMonitor() {
  const [jobs, setJobs] = useState([])
  const [error, setError] = useState(null)
  const [selectedJobId, setSelectedJobId] = useState(null)
  const [jobDetail, setJobDetail] = useState(null)
  const [logs, setLogs] = useState([])
  const [autoScroll, setAutoScroll] = useState(true)
  const [sortKey, setSortKey] = useState('name')
  const [sortDir, setSortDir] = useState('asc')
  const [actionLoading, setActionLoading] = useState(null)
  const logRef = useRef(null)

  // Fetch job list
  const fetchJobs = useCallback(async () => {
    try {
      const data = await get('/processes')
      setJobs(data.jobs)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [])

  // Fetch selected job detail + logs
  const fetchDetail = useCallback(async (jobId) => {
    if (!jobId) return
    try {
      const [detail, logData] = await Promise.all([
        get(`/processes/${jobId}`),
        get(`/processes/${jobId}/logs`),
      ])
      setJobDetail(detail)
      setLogs(logData.logs)
    } catch {
      // non-critical — keep stale data
    }
  }, [])

  // Initial fetch
  useEffect(() => {
    fetchJobs()
  }, [fetchJobs])

  // Poll jobs every 5s
  useEffect(() => {
    const id = setInterval(fetchJobs, 5000)
    return () => clearInterval(id)
  }, [fetchJobs])

  // Poll detail every 5s when a job is selected
  useEffect(() => {
    if (!selectedJobId) {
      setJobDetail(null)
      setLogs([])
      return
    }
    fetchDetail(selectedJobId)
    const id = setInterval(() => fetchDetail(selectedJobId), 5000)
    return () => clearInterval(id)
  }, [selectedJobId, fetchDetail])

  // Auto-scroll logs
  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  const handleAction = async (jobId, action, e) => {
    e.stopPropagation()
    setActionLoading(`${jobId}-${action}`)
    try {
      await post(`/processes/${jobId}/${action}`)
      await fetchJobs()
      if (selectedJobId === jobId) {
        await fetchDetail(jobId)
      }
    } catch {
      // action failed — next poll will update
    } finally {
      setActionLoading(null)
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

  const runningCount = jobs.filter((j) => j.running).length

  // Scraper detail rendering
  const renderScraperDetail = () => {
    if (!jobDetail?.monitor) return null
    const { scraper, current_cycle, per_subreddit } = jobDetail.monitor
    const totalSubs = current_cycle.subreddits_completed + current_cycle.subreddits_remaining
    const pct = totalSubs > 0 ? Math.round((current_cycle.subreddits_completed / totalSubs) * 100) : 0

    const sortedSubs = [...(per_subreddit || [])].sort((a, b) => {
      let aVal = a[sortKey]
      let bVal = b[sortKey]
      if (sortKey === 'name') {
        aVal = (aVal || '').toLowerCase()
        bVal = (bVal || '').toLowerCase()
        return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
      }
      if (sortKey === 'last_fetched') {
        aVal = aVal ? new Date(aVal).getTime() : 0
        bVal = bVal ? new Date(bVal).getTime() : 0
      }
      return sortDir === 'asc' ? (aVal || 0) - (bVal || 0) : (bVal || 0) - (aVal || 0)
    })

    return (
      <>
        {/* Cycle Stats */}
        <div className="dash-card">
          <h2>Scraper Stats</h2>
          <div className="scraper-stats-grid">
            <div className="stat-item">
              <div className="stat-value">{formatUptime(scraper.uptime_seconds)}</div>
              <div className="stat-label">Uptime</div>
            </div>
            <div className="stat-item">
              <div className="stat-value">{scraper.total_cycles_completed}</div>
              <div className="stat-label">Cycles</div>
            </div>
            <div className="stat-item">
              <div className="stat-value">{scraper.total_posts_collected.toLocaleString()}</div>
              <div className="stat-label">Posts Collected</div>
            </div>
            <div className="stat-item">
              <div className="stat-value">{scraper.total_errors}</div>
              <div className="stat-label">Errors</div>
            </div>
          </div>
        </div>

        {/* Progress */}
        <div className="dash-card">
          <h2>Current Cycle #{current_cycle.cycle_number}</h2>
          <div className="scraper-progress">
            <div className="scraper-progress-label">
              Subreddit {current_cycle.subreddits_completed} of {totalSubs} ({pct}%)
              {current_cycle.current_subreddit && ` — fetching r/${current_cycle.current_subreddit}`}
            </div>
            <div className="scraper-progress-bar">
              <div className="scraper-progress-fill" style={{ width: `${pct}%` }} />
            </div>
          </div>
          <div className="scraper-stats-grid" style={{ marginTop: 14 }}>
            <div className="stat-item">
              <div className="stat-value">{current_cycle.posts_this_cycle}</div>
              <div className="stat-label">Posts This Cycle</div>
            </div>
            <div className="stat-item">
              <div className="stat-value">{current_cycle.errors_this_cycle}</div>
              <div className="stat-label">Errors This Cycle</div>
            </div>
          </div>
        </div>

        {/* Per-Subreddit Table */}
        {per_subreddit && per_subreddit.length > 0 && (
          <div className="dash-card">
            <h2>Per-Subreddit Status</h2>
            <div className="scraper-sub-table-wrap">
              <table className="scraper-sub-table">
                <thead>
                  <tr>
                    <th className="sortable" onClick={() => handleSort('name')}>
                      Subreddit{sortIndicator('name')}
                    </th>
                    <th>Status</th>
                    <th className="sortable" onClick={() => handleSort('posts_last_cycle')}>
                      Last Cycle{sortIndicator('posts_last_cycle')}
                    </th>
                    <th className="sortable" onClick={() => handleSort('total_posts')}>
                      Total Posts{sortIndicator('total_posts')}
                    </th>
                    <th className="sortable" onClick={() => handleSort('last_fetched')}>
                      Last Fetched{sortIndicator('last_fetched')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedSubs.map((sub) => (
                    <tr key={sub.name}>
                      <td style={{ fontWeight: 600 }}>r/{sub.name}</td>
                      <td>
                        <span className={`sub-status-badge ${sub.status}`}>
                          {sub.status}
                        </span>
                      </td>
                      <td>{sub.posts_last_cycle}</td>
                      <td>{sub.total_posts.toLocaleString()}</td>
                      <td title={sub.last_fetched || ''}>{formatTime(sub.last_fetched)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </>
    )
  }

  // Generic job detail rendering
  const renderGenericDetail = () => {
    if (!jobDetail) return null
    return (
      <div className="dash-card">
        <h2>Job Details</h2>
        <div className="generic-detail-grid">
          <div className="detail-field">
            <span className="detail-field-label">Started</span>
            <span className="detail-field-value">{jobDetail.started_at ? formatTime(jobDetail.started_at) : '—'}</span>
          </div>
          <div className="detail-field">
            <span className="detail-field-label">Completed</span>
            <span className="detail-field-value">{jobDetail.completed_at ? formatTime(jobDetail.completed_at) : '—'}</span>
          </div>
          {jobDetail.error && (
            <div className="detail-field">
              <span className="detail-field-label">Error</span>
              <span className="detail-field-value detail-error">{jobDetail.error}</span>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="process-monitor">
      <div className="process-header">
        <h1>Processes</h1>
        <span className="process-summary">
          {runningCount} of {jobs.length} running
        </span>
      </div>

      {error && <div className="process-error">Failed to load: {error}</div>}

      {/* Job Cards */}
      <div className="job-cards-grid">
        {jobs.map((job) => (
          <div
            key={job.id}
            className={`job-card${selectedJobId === job.id ? ' selected' : ''}`}
            onClick={() => setSelectedJobId(selectedJobId === job.id ? null : job.id)}
          >
            <div className="job-card-top">
              <span className="job-card-name">{job.name}</span>
              <span className={`job-status-dot ${job.running ? 'running' : 'stopped'}`} />
            </div>
            {job.description && <div className="job-card-desc">{job.description}</div>}
            <div className="job-card-meta">
              <span className="job-type-badge">{job.type}</span>
            </div>
            <div className="job-card-actions">
              {!job.running && (
                <button
                  className="job-action-btn"
                  onClick={(e) => handleAction(job.id, 'start', e)}
                  disabled={actionLoading === `${job.id}-start`}
                >
                  <FiPlay size={12} /> Start
                </button>
              )}
              {job.running && (
                <button
                  className="job-action-btn stop"
                  onClick={(e) => handleAction(job.id, 'stop', e)}
                  disabled={actionLoading === `${job.id}-stop`}
                >
                  <FiSquare size={12} /> Stop
                </button>
              )}
              <button
                className="job-action-btn"
                onClick={(e) => handleAction(job.id, 'restart', e)}
                disabled={actionLoading === `${job.id}-restart`}
              >
                <FiRefreshCw size={12} /> Restart
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Detail Panel */}
      {selectedJobId && jobDetail && (
        <div className="job-detail-panel">
          {jobDetail.monitor ? renderScraperDetail() : renderGenericDetail()}

          {/* Log Viewer */}
          <div className="dash-card">
            <div className="log-viewer-header">
              <h2>Logs</h2>
              <button
                className={`log-auto-scroll-toggle${autoScroll ? ' active' : ''}`}
                onClick={() => setAutoScroll((v) => !v)}
              >
                Auto-scroll {autoScroll ? 'ON' : 'OFF'}
              </button>
            </div>
            <div className="log-container" ref={logRef}>
              {logs.length === 0 && <div className="log-empty">No log entries</div>}
              {logs.map((entry, i) => (
                <div key={i} className={`log-entry ${entry.level}`}>
                  <span className="log-ts">{formatLogTs(entry.timestamp)}</span>
                  {entry.message}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
