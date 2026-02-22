import { useEffect, useState, useCallback, useRef } from 'react'
import { FiPlay, FiSquare, FiRefreshCw, FiEdit2, FiCheck, FiX } from 'react-icons/fi'
import { get, post, put } from '../api/client'
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

function formatCountdown(seconds) {
  if (seconds == null || seconds <= 0) return '0s'
  if (seconds < 60) return `${Math.floor(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}m ${s}s`
}

function scheduleLabel(schedule) {
  if (!schedule) return null
  const mins = schedule.interval_minutes
  if (schedule.interval_type === 'after_completion') return `${mins}m cooldown`
  return `every ${mins}m`
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
  const [paramValues, setParamValues] = useState({})
  const [editMode, setEditMode] = useState(false)
  const [editConfig, setEditConfig] = useState({})
  const [saveLoading, setSaveLoading] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const logRef = useRef(null)
  const prevSelectedRef = useRef(null)
  const [now, setNow] = useState(Date.now())

  // Tick every second for countdown display
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

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

  // Initialize param values and reset edit mode when selecting a job
  useEffect(() => {
    if (selectedJobId && selectedJobId !== prevSelectedRef.current) {
      setEditMode(false)
      setSaveError(null)
      const job = jobs.find((j) => j.id === selectedJobId)
      if (job) {
        // Use current_params if available (reflects running/last-run values), else defaults
        if (job.current_params && Object.keys(job.current_params).length > 0) {
          setParamValues({ ...job.current_params })
        } else {
          const defaults = {}
          for (const p of job.params || []) {
            defaults[p.key] = p.default
          }
          setParamValues(defaults)
        }
      }
    }
    prevSelectedRef.current = selectedJobId
  }, [selectedJobId, jobs])

  // Auto-scroll logs
  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  const handleAction = async (jobId, action) => {
    setActionLoading(`${jobId}-${action}`)
    try {
      const body = (action === 'start' || action === 'restart')
        ? { params: paramValues }
        : undefined
      await post(`/processes/${jobId}/${action}`, body)
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

  const handleParamChange = (key, value) => {
    setParamValues((prev) => ({ ...prev, [key]: value }))
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

  const enterEditMode = () => {
    if (!selectedJob) return
    setEditConfig({
      name: selectedJob.name,
      description: selectedJob.description || '',
      type: selectedJob.type,
      on_failure: selectedJob.on_failure || 'stop',
      auto_start: selectedJob.auto_start || false,
      schedule_enabled: !!selectedJob.schedule,
      interval_minutes: selectedJob.schedule?.interval_minutes || 30,
      interval_type: selectedJob.schedule?.interval_type || 'after_completion',
    })
    setSaveError(null)
    setEditMode(true)
  }

  const cancelEdit = () => {
    setEditMode(false)
    setSaveError(null)
  }

  const handleSaveConfig = async () => {
    if (!selectedJob) return
    setSaveLoading(true)
    setSaveError(null)
    try {
      const payload = {
        name: editConfig.name,
        description: editConfig.description,
        type: editConfig.type,
        auto_start: editConfig.auto_start,
      }
      if (editConfig.type === 'continuous') {
        payload.on_failure = editConfig.on_failure
        payload.schedule = null
      } else {
        payload.on_failure = 'stop'
        if (editConfig.schedule_enabled) {
          payload.schedule = {
            interval_minutes: parseInt(editConfig.interval_minutes, 10) || 30,
            interval_type: editConfig.interval_type,
          }
        } else {
          payload.schedule = null
        }
      }
      await put(`/processes/${selectedJob.id}/config`, payload)
      setEditMode(false)
      await fetchJobs()
      await fetchDetail(selectedJob.id)
    } catch (err) {
      setSaveError(err.message || 'Failed to save')
    } finally {
      setSaveLoading(false)
    }
  }

  const updateEditField = (key, value) => {
    setEditConfig((prev) => ({ ...prev, [key]: value }))
  }

  const runningCount = jobs.filter((j) => j.running || j.schedule_active).length
  const selectedJob = jobs.find((j) => j.id === selectedJobId)

  // Action buttons for detail panel
  const renderDetailActions = () => {
    if (!selectedJob) return null
    const isActive = selectedJob.running || selectedJob.schedule_active
    return (
      <div className="detail-actions-header">
        <h2>{selectedJob.name}</h2>
        <div className="detail-actions">
          {!isActive && (
            <button
              className="job-action-btn"
              onClick={() => handleAction(selectedJob.id, 'start')}
              disabled={actionLoading === `${selectedJob.id}-start`}
            >
              <FiPlay size={12} /> Start
            </button>
          )}
          {isActive && (
            <button
              className="job-action-btn stop"
              onClick={() => handleAction(selectedJob.id, 'stop')}
              disabled={actionLoading === `${selectedJob.id}-stop`}
            >
              <FiSquare size={12} /> Stop
            </button>
          )}
          <button
            className="job-action-btn"
            onClick={() => handleAction(selectedJob.id, 'restart')}
            disabled={actionLoading === `${selectedJob.id}-restart`}
          >
            <FiRefreshCw size={12} /> Restart
          </button>
        </div>
      </div>
    )
  }

  // Schedule status section
  const renderScheduleStatus = () => {
    if (!selectedJob?.schedule || !jobDetail) return null
    const detail = jobDetail
    if (!detail.schedule_active) return null

    const nextRunIso = detail.next_run_at
    let countdownSecs = null
    let stateLabel = 'Waiting'
    if (detail.running) {
      stateLabel = 'Running cycle'
    } else if (nextRunIso) {
      countdownSecs = Math.max(0, Math.floor((new Date(nextRunIso).getTime() - now) / 1000))
      stateLabel = `Waiting — next run in ${formatCountdown(countdownSecs)}`
    }

    return (
      <div className="dash-card schedule-status-card">
        <h2>Schedule</h2>
        <div className="schedule-status-content">
          <span className={`schedule-state-badge ${detail.running ? 'running' : 'waiting'}`}>
            {stateLabel}
          </span>
          <span className="schedule-type-label">
            {scheduleLabel(selectedJob.schedule)}
          </span>
        </div>
      </div>
    )
  }

  // Config editor (read-only display + edit form)
  const renderConfigEditor = () => {
    if (!selectedJob) return null
    const isActive = selectedJob.running || selectedJob.schedule_active

    if (!editMode) {
      return (
        <div className="dash-card">
          <div className="config-editor-header">
            <h2>Configuration</h2>
            {!isActive && (
              <button className="job-action-btn" onClick={enterEditMode}>
                <FiEdit2 size={12} /> Edit
              </button>
            )}
          </div>
          <div className="config-display-grid">
            <div className="detail-field">
              <span className="detail-field-label">Type</span>
              <span className="detail-field-value">{selectedJob.type}</span>
            </div>
            {selectedJob.type === 'continuous' && (
              <div className="detail-field">
                <span className="detail-field-label">On Failure</span>
                <span className="detail-field-value">
                  {selectedJob.on_failure === 'restart' ? 'Restart (10s delay)' : 'Stop'}
                </span>
              </div>
            )}
            {selectedJob.type === 'oneshot' && (
              <div className="detail-field">
                <span className="detail-field-label">Schedule</span>
                <span className="detail-field-value">
                  {scheduleLabel(selectedJob.schedule) || 'None'}
                </span>
              </div>
            )}
            <div className="detail-field">
              <span className="detail-field-label">Auto Start</span>
              <span className="detail-field-value">{selectedJob.auto_start ? 'Yes' : 'No'}</span>
            </div>
          </div>
        </div>
      )
    }

    // Edit mode
    return (
      <div className="dash-card">
        <div className="config-editor-header">
          <h2>Edit Configuration</h2>
          <div className="detail-actions">
            <button
              className="job-action-btn"
              onClick={handleSaveConfig}
              disabled={saveLoading}
            >
              <FiCheck size={12} /> {saveLoading ? 'Saving...' : 'Save'}
            </button>
            <button className="job-action-btn" onClick={cancelEdit} disabled={saveLoading}>
              <FiX size={12} /> Cancel
            </button>
          </div>
        </div>
        {saveError && <div className="config-save-error">{saveError}</div>}
        <div className="config-form">
          <div className="config-field">
            <label className="param-label">Name</label>
            <input
              className="config-input"
              type="text"
              value={editConfig.name || ''}
              onChange={(e) => updateEditField('name', e.target.value)}
            />
          </div>
          <div className="config-field">
            <label className="param-label">Description</label>
            <input
              className="config-input"
              type="text"
              value={editConfig.description || ''}
              onChange={(e) => updateEditField('description', e.target.value)}
            />
          </div>
          <div className="config-field-row">
            <div className="config-field">
              <label className="param-label">Type</label>
              <select
                className="config-input"
                value={editConfig.type}
                onChange={(e) => updateEditField('type', e.target.value)}
              >
                <option value="continuous">Continuous</option>
                <option value="oneshot">Oneshot</option>
              </select>
            </div>
            <div className="config-field">
              <label className="param-label">Auto Start</label>
              <button
                className={`config-toggle${editConfig.auto_start ? ' active' : ''}`}
                onClick={() => updateEditField('auto_start', !editConfig.auto_start)}
                type="button"
              >
                {editConfig.auto_start ? 'ON' : 'OFF'}
              </button>
            </div>
          </div>
          {editConfig.type === 'continuous' && (
            <div className="config-field">
              <label className="param-label">On Failure</label>
              <select
                className="config-input"
                value={editConfig.on_failure}
                onChange={(e) => updateEditField('on_failure', e.target.value)}
              >
                <option value="stop">Stop</option>
                <option value="restart">Restart (10s delay)</option>
              </select>
            </div>
          )}
          {editConfig.type === 'oneshot' && (
            <>
              <div className="config-field">
                <label className="param-label">Schedule</label>
                <button
                  className={`config-toggle${editConfig.schedule_enabled ? ' active' : ''}`}
                  onClick={() => updateEditField('schedule_enabled', !editConfig.schedule_enabled)}
                  type="button"
                >
                  {editConfig.schedule_enabled ? 'ON' : 'OFF'}
                </button>
              </div>
              {editConfig.schedule_enabled && (
                <div className="config-schedule-fields">
                  <div className="config-field">
                    <label className="param-label">Interval (minutes)</label>
                    <input
                      className="config-input"
                      type="number"
                      min={1}
                      value={editConfig.interval_minutes}
                      onChange={(e) => updateEditField('interval_minutes', e.target.value)}
                    />
                  </div>
                  <div className="config-field">
                    <label className="param-label">Interval Type</label>
                    <select
                      className="config-input"
                      value={editConfig.interval_type}
                      onChange={(e) => updateEditField('interval_type', e.target.value)}
                    >
                      <option value="after_completion">After Completion</option>
                      <option value="interval">Fixed Interval</option>
                    </select>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    )
  }

  // Parameter inputs form
  const renderParamInputs = () => {
    if (!selectedJob?.params?.length) return null
    return (
      <div className="dash-card">
        <h2>Parameters</h2>
        <div className="param-inputs-grid">
          {selectedJob.params.map((p) => (
            <div key={p.key} className="param-field">
              <label className="param-label">
                {p.label}
                {p.unit && <span className="param-unit">({p.unit})</span>}
              </label>
              <input
                className="param-input"
                type="number"
                value={paramValues[p.key] ?? p.default}
                min={p.min}
                max={p.max}
                step={p.step}
                disabled={selectedJob.running || selectedJob.schedule_active}
                onChange={(e) => handleParamChange(p.key, parseFloat(e.target.value))}
              />
              {p.description && <span className="param-description">{p.description}</span>}
            </div>
          ))}
        </div>
      </div>
    )
  }

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
              <span className={`job-status-dot ${(job.running || job.schedule_active) ? 'running' : 'stopped'}`} />
            </div>
            {job.description && <div className="job-card-desc">{job.description}</div>}
            <div className="job-card-meta">
              <span className="job-type-badge">
                {scheduleLabel(job.schedule) || job.type}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Detail Panel */}
      {selectedJobId && jobDetail && (
        <div className="job-detail-panel">
          {renderDetailActions()}
          {renderConfigEditor()}
          {renderScheduleStatus()}
          {renderParamInputs()}
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
