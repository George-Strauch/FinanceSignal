import { useEffect, useState, useCallback, useRef } from 'react'
import { FiPlay, FiSquare, FiRefreshCw, FiEdit2, FiCheck, FiX, FiClock, FiTarget, FiActivity } from 'react-icons/fi'
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

function formatDuration(isoA, isoB) {
  if (!isoA || !isoB) return '—'
  const ms = Math.abs(new Date(isoB).getTime() - new Date(isoA).getTime())
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`
  return `${Math.floor(ms / 3600000)}h ${Math.floor((ms % 3600000) / 60000)}m`
}

// Derive a run mode from type + schedule:
//   continuous → runs forever until stopped
//   oneshot + schedule → recurring scheduled job (runs, waits, repeats)
//   oneshot + no schedule → manual one-shot (runs once when triggered)
function runMode(job) {
  if (job.type === 'continuous') return 'continuous'
  if (job.schedule) return 'scheduled'
  return 'manual'
}

function runModeIcon(mode) {
  if (mode === 'continuous') return <FiActivity size={11} />
  if (mode === 'scheduled') return <FiClock size={11} />
  return <FiTarget size={11} />
}

function runModeLabel(mode) {
  if (mode === 'continuous') return 'Continuous'
  if (mode === 'scheduled') return 'Scheduled'
  return 'Manual'
}

function runModeDetail(job) {
  const mode = runMode(job)
  if (mode === 'continuous') return 'Runs continuously until stopped'
  if (mode === 'scheduled') return `Scheduled — ${scheduleLabel(job.schedule)}`
  return 'Manual — run on demand'
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
  const [fetchQueue, setFetchQueue] = useState(null)
  const [nerQueue, setNerQueue] = useState(null)
  const [relevanceQueue, setRelevanceQueue] = useState(null)
  const [tab, setTab] = useState('processes')
  const [queuesData, setQueuesData] = useState(null)
  const [queueFilter, setQueueFilter] = useState({ queue: null, phase: null, outcome: null })
  const [retrying, setRetrying] = useState(false)
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
      setFetchQueue(null)
      setNerQueue(null)
      setRelevanceQueue(null)
      return
    }
    fetchDetail(selectedJobId)
    const id = setInterval(() => fetchDetail(selectedJobId), 5000)
    return () => clearInterval(id)
  }, [selectedJobId, fetchDetail])

  // Fetch queue for reddit_scraper + backfetch
  const [pastLimit, setPastLimit] = useState(50)
  const pastLimitRef = useRef(50)
  pastLimitRef.current = pastLimit

  const fetchFetchQueue = useCallback(async (jobId) => {
    try {
      const data = await get(`/processes/${jobId}/fetch-queue?past_limit=${pastLimitRef.current}`)
      setFetchQueue(data)
    } catch {
      // 404 for non-fetch jobs — just clear
      setFetchQueue(null)
    }
  }, [])

  useEffect(() => {
    if (selectedJobId !== 'reddit_scraper' && selectedJobId !== 'backfetch') {
      setFetchQueue(null)
      setPastLimit(50)
      return
    }
    fetchFetchQueue(selectedJobId)
    const id = setInterval(() => fetchFetchQueue(selectedJobId), 3000)
    return () => clearInterval(id)
  }, [selectedJobId, fetchFetchQueue])

  // Load more past rows
  const loadMorePast = useCallback(() => {
    if (!fetchQueue) return
    if (fetchQueue.past.length >= fetchQueue.past_total) return
    setPastLimit((l) => l + 50)
    fetchFetchQueue(selectedJobId)
  }, [fetchQueue, fetchFetchQueue, selectedJobId])

  // Scroll handler for past table
  const pastTableRef = useRef(null)
  const handlePastScroll = useCallback((e) => {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 50) {
      loadMorePast()
    }
  }, [loadMorePast])

  // ── NER queue ──────────────────────────────────────────────────
  const [nerPastLimit, setNerPastLimit] = useState(50)
  const nerPastLimitRef = useRef(50)
  nerPastLimitRef.current = nerPastLimit

  const fetchNerQueue = useCallback(async (jobId) => {
    try {
      const data = await get(`/processes/${jobId}/ner-queue?past_limit=${nerPastLimitRef.current}`)
      setNerQueue(data)
    } catch {
      setNerQueue(null)
    }
  }, [])

  useEffect(() => {
    if (selectedJobId !== 'ner_extraction') {
      setNerQueue(null)
      setNerPastLimit(50)
      return
    }
    fetchNerQueue(selectedJobId)
    const id = setInterval(() => fetchNerQueue(selectedJobId), 3000)
    return () => clearInterval(id)
  }, [selectedJobId, fetchNerQueue])

  const loadMoreNerPast = useCallback(() => {
    if (!nerQueue) return
    if (nerQueue.past.length >= nerQueue.past_total) return
    setNerPastLimit((l) => l + 50)
    fetchNerQueue('ner_extraction')
  }, [nerQueue, fetchNerQueue])

  const nerPastTableRef = useRef(null)
  const handleNerPastScroll = useCallback((e) => {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 50) {
      loadMoreNerPast()
    }
  }, [loadMoreNerPast])

  // ── Relevance queue ────────────────────────────────────────────
  const [relPastLimit, setRelPastLimit] = useState(50)
  const relPastLimitRef = useRef(50)
  relPastLimitRef.current = relPastLimit

  const fetchRelevanceQueue = useCallback(async (jobId) => {
    try {
      const data = await get(`/processes/${jobId}/relevance-queue?past_limit=${relPastLimitRef.current}`)
      setRelevanceQueue(data)
    } catch {
      setRelevanceQueue(null)
    }
  }, [])

  useEffect(() => {
    if (selectedJobId !== 'relevance_scoring' && selectedJobId !== 'relevance_backfill') {
      setRelevanceQueue(null)
      setRelPastLimit(50)
      return
    }
    fetchRelevanceQueue(selectedJobId)
    const id = setInterval(() => fetchRelevanceQueue(selectedJobId), 3000)
    return () => clearInterval(id)
  }, [selectedJobId, fetchRelevanceQueue])

  const loadMoreRelPast = useCallback(() => {
    if (!relevanceQueue) return
    if (relevanceQueue.past.length >= relevanceQueue.past_total) return
    setRelPastLimit((l) => l + 50)
    fetchRelevanceQueue(selectedJobId)
  }, [relevanceQueue, fetchRelevanceQueue, selectedJobId])

  const relPastTableRef = useRef(null)
  const handleRelPastScroll = useCallback((e) => {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 50) {
      loadMoreRelPast()
    }
  }, [loadMoreRelPast])

  // ── Unified Queues view ────────────────────────────────────────────
  const [queueLimit, setQueueLimit] = useState(100)

  const fetchQueues = useCallback(async () => {
    const params = new URLSearchParams()
    if (queueFilter.queue) params.set('queue', queueFilter.queue)
    if (queueFilter.phase) params.set('phase', queueFilter.phase)
    if (queueFilter.outcome) params.set('outcome', queueFilter.outcome)
    params.set('limit', queueLimit)
    params.set('offset', 0)
    try {
      const data = await get(`/processes/queues/all?${params.toString()}`)
      setQueuesData(data)
    } catch {
      setQueuesData(null)
    }
  }, [queueFilter, queueLimit])

  useEffect(() => {
    if (tab !== 'queues') return
    fetchQueues()
    const id = setInterval(fetchQueues, 5000)
    return () => clearInterval(id)
  }, [tab, fetchQueues])

  const retryFailed = useCallback(async () => {
    setRetrying(true)
    try {
      const body = queueFilter.queue ? { queue: queueFilter.queue } : {}
      const res = await post('/processes/queues/retry', body)
      const total = res?.total ?? 0
      if (total > 0) {
        await fetchQueues()
      }
    } catch (e) {
      console.error('Retry failed:', e)
    } finally {
      setRetrying(false)
    }
  }, [queueFilter.queue, fetchQueues])

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
              <span className="detail-field-label">Run Mode</span>
              <span className={`detail-field-value mode-badge mode-${runMode(selectedJob)}`}>
                {runModeIcon(runMode(selectedJob))}
                {runModeDetail(selectedJob)}
              </span>
            </div>
            {selectedJob.type === 'continuous' && (
              <div className="detail-field">
                <span className="detail-field-label">On Failure</span>
                <span className="detail-field-value">
                  {selectedJob.on_failure === 'restart' ? 'Restart (10s delay)' : 'Stop'}
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
              <label className="param-label">Run Mode</label>
              <select
                className="config-input"
                value={
                  editConfig.type === 'continuous' ? 'continuous'
                  : editConfig.schedule_enabled ? 'scheduled'
                  : 'manual'
                }
                onChange={(e) => {
                  const v = e.target.value
                  if (v === 'continuous') {
                    updateEditField('type', 'continuous')
                    updateEditField('schedule_enabled', false)
                  } else if (v === 'scheduled') {
                    updateEditField('type', 'oneshot')
                    updateEditField('schedule_enabled', true)
                  } else {
                    updateEditField('type', 'oneshot')
                    updateEditField('schedule_enabled', false)
                  }
                }}
              >
                <option value="manual">Manual — run on demand</option>
                <option value="scheduled">Scheduled — recurring interval</option>
                <option value="continuous">Continuous — runs until stopped</option>
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
          {editConfig.type === 'oneshot' && editConfig.schedule_enabled && (
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
          {selectedJob.params.map((p) => {
            const isText = p.type === 'text'
            const isBoolean = p.type === 'boolean'
            const isNumber = !isText && !isBoolean
            return (
              <div key={p.key} className="param-field">
                <label className="param-label">
                  {p.label}
                  {p.unit && <span className="param-unit">({p.unit})</span>}
                </label>
                {isBoolean ? (
                  <label className="param-toggle">
                    <input
                      type="checkbox"
                      checked={!!paramValues[p.key] ?? p.default}
                      disabled={selectedJob.running || selectedJob.schedule_active}
                      onChange={(e) => handleParamChange(p.key, e.target.checked)}
                    />
                    <span className="param-toggle-label">
                      {paramValues[p.key] ? 'Enabled' : 'Disabled'}
                    </span>
                  </label>
                ) : (
                  <input
                    className="param-input"
                    type={isText ? 'text' : 'number'}
                    value={paramValues[p.key] ?? p.default}
                    {...(isNumber && { min: p.min, max: p.max, step: p.step })}
                    disabled={selectedJob.running || selectedJob.schedule_active}
                    onChange={(e) => handleParamChange(
                      p.key,
                      isText ? e.target.value : parseFloat(e.target.value),
                    )}
                  />
                )}
                {p.description && <span className="param-description">{p.description}</span>}
              </div>
            )
          })}
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
              <div className="stat-value">{(scraper.total_comments_collected || 0).toLocaleString()}</div>
              <div className="stat-label">Comments Collected</div>
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
              <div className="stat-value">{(current_cycle.comments_this_cycle || 0).toLocaleString()}</div>
              <div className="stat-label">Comments This Cycle</div>
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
    const m = jobDetail.monitor
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
          {m && (
            <>
              <div className="detail-field">
                <span className="detail-field-label">Phase</span>
                <span className="detail-field-value">{m.current_phase || '—'}</span>
              </div>
              {m.sources_processed != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Sources Processed</span>
                  <span className="detail-field-value">{m.sources_processed.toLocaleString()}</span>
                </div>
              )}
              {m.entities_found != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Entities Found</span>
                  <span className="detail-field-value">{m.entities_found.toLocaleString()}</span>
                </div>
              )}
              {m.tickers_found != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Tickers Found</span>
                  <span className="detail-field-value">{m.tickers_found.toLocaleString()}</span>
                </div>
              )}
              {m.relevance_enqueued != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Relevance Enqueued</span>
                  <span className="detail-field-value">{m.relevance_enqueued.toLocaleString()}</span>
                </div>
              )}
              {m.pairs_scored != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Pairs Scored</span>
                  <span className="detail-field-value">{m.pairs_scored.toLocaleString()}</span>
                </div>
              )}
              {m.pairs_requeued != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Pairs Requeued</span>
                  <span className="detail-field-value">{m.pairs_requeued.toLocaleString()}</span>
                </div>
              )}
              {m.pairs_failed != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Pairs Failed</span>
                  <span className="detail-field-value">{m.pairs_failed.toLocaleString()}</span>
                </div>
              )}
              {m.ticker_pairs_enqueued != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Ticker Pairs Enqueued</span>
                  <span className="detail-field-value">{m.ticker_pairs_enqueued.toLocaleString()}</span>
                </div>
              )}
              {m.ner_pairs_enqueued != null && (
                <div className="detail-field">
                  <span className="detail-field-label">NER Pairs Enqueued</span>
                  <span className="detail-field-value">{m.ner_pairs_enqueued.toLocaleString()}</span>
                </div>
              )}
              {m.posts_new != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Posts New</span>
                  <span className="detail-field-value">{m.posts_new.toLocaleString()}</span>
                </div>
              )}
              {m.posts_updated != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Posts Updated</span>
                  <span className="detail-field-value">{m.posts_updated.toLocaleString()}</span>
                </div>
              )}
              {m.pages_fetched != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Pages Fetched</span>
                  <span className="detail-field-value">{m.pages_fetched.toLocaleString()}</span>
                </div>
              )}
              {m.errors != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Errors</span>
                  <span className="detail-field-value">{m.errors.toLocaleString()}</span>
                </div>
              )}
              {m.empty_polls != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Empty Polls</span>
                  <span className="detail-field-value">{m.empty_polls.toLocaleString()}</span>
                </div>
              )}
              {m.tickers_total != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Tickers Total</span>
                  <span className="detail-field-value">{m.tickers_total.toLocaleString()}</span>
                </div>
              )}
              {m.tickers_fetched != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Tickers Fetched</span>
                  <span className="detail-field-value">{m.tickers_fetched.toLocaleString()}</span>
                </div>
              )}
              {m.tickers_skipped != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Tickers Skipped</span>
                  <span className="detail-field-value">{m.tickers_skipped.toLocaleString()}</span>
                </div>
              )}
              {m.tickers_failed != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Tickers Failed</span>
                  <span className="detail-field-value">{m.tickers_failed.toLocaleString()}</span>
                </div>
              )}
              {m.tickers_rate_limited != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Rate Limited</span>
                  <span className="detail-field-value">{m.tickers_rate_limited.toLocaleString()}</span>
                </div>
              )}
              {m.rows_inserted != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Rows Inserted</span>
                  <span className="detail-field-value">{m.rows_inserted.toLocaleString()}</span>
                </div>
              )}
              {m.current_ticker != null && m.current_ticker !== '' && (
                <div className="detail-field">
                  <span className="detail-field-label">Current Ticker</span>
                  <span className="detail-field-value">{m.current_ticker}</span>
                </div>
              )}
              {m.consecutive_failures != null && m.consecutive_failures > 0 && (
                <div className="detail-field">
                  <span className="detail-field-label">Consecutive Failures</span>
                  <span className="detail-field-value">{m.consecutive_failures}</span>
                </div>
              )}
              {m.in_cooldown != null && m.in_cooldown && (
                <div className="detail-field">
                  <span className="detail-field-label">Cooldown Until</span>
                  <span className="detail-field-value">{m.cooldown_until ? formatTime(m.cooldown_until) : '—'}</span>
                </div>
              )}
              {m.last_cycle_duration != null && (
                <div className="detail-field">
                  <span className="detail-field-label">Last Cycle</span>
                  <span className="detail-field-value">{m.last_cycle_duration}s</span>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    )
  }

  // Fetch Queue rendering (reddit_scraper + backfetch)
  const renderFetchQueue = () => {
    if (!fetchQueue) return null
    const { ready, past, stats } = fetchQueue

    const statusDot = (status) => {
      if (status === 'success') return 'fq-dot-success'
      if (status === 'failed') return 'fq-dot-failed'
      if (status === 'in_progress') return 'fq-dot-progress'
      return 'fq-dot-ready'
    }

    return (
      <>
        {/* Queue summary stats */}
        <div className="dash-card">
          <h2>Fetch Queue</h2>
          <div className="fq-stats-grid">
            <div className="stat-item">
              <div className="stat-value">{stats.ready || 0}</div>
              <div className="stat-label">Ready</div>
            </div>
            <div className="stat-item">
              <div className="stat-value">{stats.in_progress || 0}</div>
              <div className="stat-label">In Progress</div>
            </div>
            <div className="stat-item">
              <div className="stat-value" style={{ color: 'rgb(46, 204, 113)' }}>{stats.success || 0}</div>
              <div className="stat-label">Success</div>
            </div>
            <div className="stat-item">
              <div className="stat-value" style={{ color: 'rgb(239, 68, 68)' }}>{stats.failed || 0}</div>
              <div className="stat-label">Failed</div>
            </div>
          </div>
        </div>

        {/* Ready / in-progress queue */}
        <div className="dash-card">
          <h2>Queue ({ready.length})</h2>
          {ready.length === 0 ? (
            <p className="fq-empty">Queue is empty — waiting for cycle to enqueue fetches.</p>
          ) : (
            <div className="fq-table-wrap">
              <table className="fq-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Subreddit</th>
                    <th>Type</th>
                    <th>Page</th>
                    <th>Status</th>
                    <th>Enqueued</th>
                    <th>URL</th>
                  </tr>
                </thead>
                <tbody>
                  {ready.map((r) => (
                    <tr key={r.id}>
                      <td><span className={`fq-status-dot ${statusDot(r.status)}`} /></td>
                      <td className="fq-sub">r/{r.subreddit}</td>
                      <td><span className={`fq-type-badge fq-type-${r.fetch_type}`}>{r.fetch_type}</span></td>
                      <td>{r.page_num}</td>
                      <td className="fq-status-cell">{r.status === 'in_progress' ? 'running…' : 'ready'}</td>
                      <td className="fq-time">{formatTime(r.enqueued_at)}</td>
                      <td className="fq-url-cell" title={r.url}>{r.url.replace('https://old.reddit.com', '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Past fetches history */}
        <div className="dash-card">
          <h2>Recent Fetches ({past.length}{fetchQueue.past_total > past.length ? ` of ${fetchQueue.past_total}` : ''})</h2>
          {past.length === 0 ? (
            <p className="fq-empty">No completed fetches yet.</p>
          ) : (
            <div className="fq-table-wrap" ref={pastTableRef} onScroll={handlePastScroll}>
              <table className="fq-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Subreddit</th>
                    <th>Type</th>
                    <th>Page</th>
                    <th>Posts</th>
                    <th>New</th>
                    <th>Status</th>
                    <th>Wait Time</th>
                    <th>Fetch Time</th>
                    <th>Completed</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {past.map((r) => (
                    <tr key={r.id} className={r.status === 'failed' ? 'fq-row-failed' : ''}>
                      <td><span className={`fq-status-dot ${statusDot(r.status)}`} /></td>
                      <td className="fq-sub">r/{r.subreddit}</td>
                      <td><span className={`fq-type-badge fq-type-${r.fetch_type}`}>{r.fetch_type}</span></td>
                      <td>{r.page_num}</td>
                      <td>{r.posts_fetched ?? '-'}</td>
                      <td>{r.posts_new ?? '-'}</td>
                      <td className={`fq-status-cell fq-status-${r.status}`}>{r.status}</td>
                      <td className="fq-time">{formatDuration(r.enqueued_at, r.fetch_completed_at)}</td>
                      <td className="fq-time">{r.fetch_duration != null ? `${r.fetch_duration.toFixed(1)}s` : '—'}</td>
                      <td className="fq-time">{formatTime(r.fetch_completed_at)}</td>
                      <td className="fq-error-cell" title={r.error || ''}>{r.error || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {past.length < fetchQueue.past_total && (
                <div className="fq-load-more">Scroll to load more ({fetchQueue.past_total - past.length} remaining)</div>
              )}
            </div>
          )}
        </div>
      </>
    )
  }

  const statusDotFn = (status) => {
    if (status === 'success') return 'fq-dot-success'
    if (status === 'failed') return 'fq-dot-failed'
    if (status === 'in_progress') return 'fq-dot-progress'
    return 'fq-dot-ready'
  }

  const renderNerQueue = () => {
    if (!nerQueue) return null
    const { ready, past, stats } = nerQueue

    return (
      <>
        <div className="dash-card">
          <h2>NER Queue</h2>
          <div className="fq-stats-grid">
            <div className="stat-item"><div className="stat-value">{stats.ready || 0}</div><div className="stat-label">Ready</div></div>
            <div className="stat-item"><div className="stat-value">{stats.in_progress || 0}</div><div className="stat-label">In Progress</div></div>
            <div className="stat-item"><div className="stat-value" style={{ color: 'rgb(46, 204, 113)' }}>{stats.success || 0}</div><div className="stat-label">Success</div></div>
            <div className="stat-item"><div className="stat-value" style={{ color: 'rgb(239, 68, 68)' }}>{stats.failed || 0}</div><div className="stat-label">Failed</div></div>
          </div>
        </div>

        <div className="dash-card">
          <h2>Queue ({ready.length})</h2>
          {ready.length === 0 ? (
            <p className="fq-empty">Queue is empty — no sources pending NER.</p>
          ) : (
            <div className="fq-table-wrap">
              <table className="fq-table">
                <thead>
                  <tr><th></th><th>Source</th><th>Subreddit</th><th>Status</th><th>Enqueued</th></tr>
                </thead>
                <tbody>
                  {ready.map((r) => (
                    <tr key={r.id}>
                      <td><span className={`fq-status-dot ${statusDotFn(r.status)}`} /></td>
                      <td className="fq-sub">{r.source_type}/{r.source_id.slice(0, 12)}</td>
                      <td>{r.subreddit || '-'}</td>
                      <td className="fq-status-cell">{r.status === 'in_progress' ? 'running…' : 'ready'}</td>
                      <td className="fq-time">{formatTime(r.enqueued_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="dash-card">
          <h2>Recent NER ({past.length}{nerQueue.past_total > past.length ? ` of ${nerQueue.past_total}` : ''})</h2>
          {past.length === 0 ? (
            <p className="fq-empty">No completed NER extractions yet.</p>
          ) : (
            <div className="fq-table-wrap" ref={nerPastTableRef} onScroll={handleNerPastScroll}>
              <table className="fq-table">
                <thead>
                  <tr><th></th><th>Source</th><th>Subreddit</th><th>Entities</th><th>Status</th><th>Wait Time</th><th>Completed</th><th>Error</th></tr>
                </thead>
                <tbody>
                  {past.map((r) => (
                    <tr key={r.id} className={r.status === 'failed' ? 'fq-row-failed' : ''}>
                      <td><span className={`fq-status-dot ${statusDotFn(r.status)}`} /></td>
                      <td className="fq-sub">{r.source_type}/{r.source_id.slice(0, 12)}</td>
                      <td>{r.subreddit || '-'}</td>
                      <td>{r.entities_found ?? '-'}</td>
                      <td className={`fq-status-cell fq-status-${r.status}`}>{r.status}</td>
                      <td className="fq-time">{formatDuration(r.enqueued_at, r.completed_at)}</td>
                      <td className="fq-time">{formatTime(r.completed_at)}</td>
                      <td className="fq-error-cell" title={r.error || ''}>{r.error || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {past.length < nerQueue.past_total && (
                <div className="fq-load-more">Scroll to load more ({nerQueue.past_total - past.length} remaining)</div>
              )}
            </div>
          )}
        </div>
      </>
    )
  }

  const renderRelevanceQueue = () => {
    if (!relevanceQueue) return null
    const { ready, past, stats } = relevanceQueue

    return (
      <>
        <div className="dash-card">
          <h2>Relevance Queue</h2>
          <div className="fq-stats-grid">
            <div className="stat-item"><div className="stat-value">{stats.ready || 0}</div><div className="stat-label">Ready</div></div>
            <div className="stat-item"><div className="stat-value">{stats.in_progress || 0}</div><div className="stat-label">In Progress</div></div>
            <div className="stat-item"><div className="stat-value" style={{ color: 'rgb(46, 204, 113)' }}>{stats.success || 0}</div><div className="stat-label">Success</div></div>
            <div className="stat-item"><div className="stat-value" style={{ color: 'rgb(239, 68, 68)' }}>{stats.failed || 0}</div><div className="stat-label">Failed</div></div>
          </div>
        </div>

        <div className="dash-card">
          <h2>Queue ({ready.length})</h2>
          {ready.length === 0 ? (
            <p className="fq-empty">Queue is empty — no pairs pending scoring.</p>
          ) : (
            <div className="fq-table-wrap">
              <table className="fq-table">
                <thead>
                  <tr><th></th><th>Source</th><th>Entity Type</th><th>Entity</th><th>Status</th><th>Enqueued</th></tr>
                </thead>
                <tbody>
                  {ready.map((r) => (
                    <tr key={r.id}>
                      <td><span className={`fq-status-dot ${statusDotFn(r.status)}`} /></td>
                      <td className="fq-sub">{r.source_type}/{r.source_id.slice(0, 12)}</td>
                      <td><span className={`fq-type-badge fq-type-${r.entity_type}`}>{r.entity_type}</span></td>
                      <td title={r.entity_text}>{r.entity_text}</td>
                      <td className="fq-status-cell">{r.status === 'in_progress' ? 'running…' : 'ready'}</td>
                      <td className="fq-time">{formatTime(r.enqueued_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="dash-card">
          <h2>Recent Scores ({past.length}{relevanceQueue.past_total > past.length ? ` of ${relevanceQueue.past_total}` : ''})</h2>
          {past.length === 0 ? (
            <p className="fq-empty">No completed scores yet.</p>
          ) : (
            <div className="fq-table-wrap" ref={relPastTableRef} onScroll={handleRelPastScroll}>
              <table className="fq-table">
                <thead>
                  <tr><th></th><th>Source</th><th>Entity Type</th><th>Entity</th><th>Score</th><th>Status</th><th>Attempts</th><th>Wait Time</th><th>Completed</th><th>Error</th></tr>
                </thead>
                <tbody>
                  {past.map((r) => (
                    <tr key={r.id} className={r.status === 'failed' ? 'fq-row-failed' : ''}>
                      <td><span className={`fq-status-dot ${statusDotFn(r.status)}`} /></td>
                      <td className="fq-sub">{r.source_type}/{r.source_id.slice(0, 12)}</td>
                      <td><span className={`fq-type-badge fq-type-${r.entity_type}`}>{r.entity_type}</span></td>
                      <td title={r.entity_text}>{r.entity_text}</td>
                      <td>{r.score != null ? r.score.toFixed(3) : '-'}</td>
                      <td className={`fq-status-cell fq-status-${r.status}`}>{r.status}</td>
                      <td>{r.attempts || 0}</td>
                      <td className="fq-time">{formatDuration(r.enqueued_at, r.completed_at)}</td>
                      <td className="fq-time">{formatTime(r.completed_at)}</td>
                      <td className="fq-error-cell" title={r.error || ''}>{r.error || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {past.length < relevanceQueue.past_total && (
                <div className="fq-load-more">Scroll to load more ({relevanceQueue.past_total - past.length} remaining)</div>
              )}
            </div>
          )}
        </div>
      </>
    )
  }

  // ── Unified Queues view ──
  const renderQueuesView = () => {
    if (!queuesData) return <div className="fq-empty">Loading queues…</div>
    const { items, total, stats, queues } = queuesData

    const QUEUE_LABELS = {
      fetch: 'Scraper / Fetch',
      ner: 'NER + Tickers',
      relevance: 'Relevance Scoring',
      yfinance: 'yfinance (Fundamentals + Price)',
      canonicalization: 'Canonicalization',
    }
    const PHASES = ['queued', 'inflight', 'completed']
    const OUTCOMES = ['success', 'failed']

    const statusDot = (phase, outcome) => {
      if (phase === 'completed') return outcome === 'success' ? 'fq-dot-success' : 'fq-dot-failed'
      if (phase === 'inflight') return 'fq-dot-progress'
      return 'fq-dot-ready'
    }

    const selectedStats = queues ? Object.entries(stats) : []

    return (
      <>
        <div className="dash-card">
          <h2>Queue Summary</h2>
          <div className="queue-stats-grid">
            {selectedStats.map(([q, s]) => (
              <div className="queue-stats-card" key={q}>
                <div className="queue-stats-name">{QUEUE_LABELS[q] || q}</div>
                <div className="queue-stats-row">
                  <span className="qstat"><b>{s.ready || 0}</b> ready</span>
                  <span className="qstat"><b>{s.in_progress || s.processing || 0}</b> inflight</span>
                  <span className="qstat qstat-success"><b>{s.success || s.done || 0}</b> success</span>
                  <span className="qstat qstat-failed"><b>{s.failed || 0}</b> failed</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="dash-card">
          <div className="queue-filters">
            <div className="queue-filter-group">
              <span className="queue-filter-label">Queue</span>
              <button
                className={`queue-filter-btn${!queueFilter.queue ? ' active' : ''}`}
                onClick={() => setQueueFilter((f) => ({ ...f, queue: null }))}
              >All</button>
              {Object.entries(QUEUE_LABELS).map(([q, label]) => (
                <button
                  key={q}
                  className={`queue-filter-btn${queueFilter.queue === q ? ' active' : ''}`}
                  onClick={() => setQueueFilter((f) => ({ ...f, queue: q }))}
                >{label}</button>
              ))}
            </div>
            <div className="queue-filter-group">
              <span className="queue-filter-label">Phase</span>
              <button
                className={`queue-filter-btn${!queueFilter.phase ? ' active' : ''}`}
                onClick={() => setQueueFilter((f) => ({ ...f, phase: null }))}
              >All</button>
              {PHASES.map((p) => (
                <button
                  key={p}
                  className={`queue-filter-btn${queueFilter.phase === p ? ' active' : ''}`}
                  onClick={() => setQueueFilter((f) => ({ ...f, phase: p }))}
                >{p[0].toUpperCase() + p.slice(1)}</button>
              ))}
            </div>
            <div className="queue-filter-group">
              <span className="queue-filter-label">Outcome</span>
              <button
                className={`queue-filter-btn${!queueFilter.outcome ? ' active' : ''}`}
                onClick={() => setQueueFilter((f) => ({ ...f, outcome: null }))}
              >All</button>
              {OUTCOMES.map((o) => (
                <button
                  key={o}
                  className={`queue-filter-btn${queueFilter.outcome === o ? ' active' : ''}`}
                  onClick={() => setQueueFilter((f) => ({ ...f, outcome: o }))}
                >{o[0].toUpperCase() + o.slice(1)}</button>
              ))}
            </div>
            <span className="queue-total">Showing {items.length} of {total.toLocaleString()}</span>
            <button
              className="queue-retry-btn"
              onClick={retryFailed}
              disabled={retrying}
              title="Move failed items back to queued for reprocessing"
            >{retrying ? 'Retrying…' : '↻ Retry Failed'}</button>
          </div>
        </div>

        <div className="dash-card">
          <h2>Queue Items ({items.length})</h2>
          {items.length === 0 ? (
            <p className="fq-empty">No queue items match the current filters.</p>
          ) : (
            <div className="fq-table-wrap">
              <table className="fq-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Queue</th>
                    <th>Subject</th>
                    <th>Detail</th>
                    <th>Phase</th>
                    <th>Outcome</th>
                    <th>Enqueued</th>
                    <th>Processed</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((r) => (
                    <tr key={`${r.queue}-${r.id}`} className={r.outcome === 'failed' ? 'fq-row-failed' : ''}>
                      <td><span className={`fq-status-dot ${statusDot(r.phase, r.outcome)}`} /></td>
                      <td><span className="qqueue-badge">{QUEUE_LABELS[r.queue] || r.queue}</span></td>
                      <td className="fq-sub" title={r.subject}>{r.subject}</td>
                      <td className="fq-sub" title={r.detail || ''}>{r.detail || '-'}</td>
                      <td className="fq-status-cell">{r.phase}</td>
                      <td className={`fq-status-cell fq-status-${r.outcome || 'pending'}`}>{r.outcome || '-'}</td>
                      <td className="fq-time">{formatTime(r.enqueued_at)}</td>
                      <td className="fq-time">{r.processed_at ? formatTime(r.processed_at) : '—'}</td>
                      <td className="fq-error-cell" title={r.message || ''}>{r.message || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </>
    )
  }

  return (
    <div className="process-monitor">
      <div className="process-header">
        <h1>Processes</h1>
        <div className="process-tabs">
          <button
            className={`process-tab${tab === 'processes' ? ' active' : ''}`}
            onClick={() => setTab('processes')}
          >Processes</button>
          <button
            className={`process-tab${tab === 'queues' ? ' active' : ''}`}
            onClick={() => setTab('queues')}
          >Queues</button>
        </div>
        <span className="process-summary">
          {tab === 'processes' ? `${runningCount} of ${jobs.length} running` : 'Unified queue monitor'}
        </span>
      </div>

      {error && <div className="process-error">Failed to load: {error}</div>}

      {tab === 'queues' ? (
        renderQueuesView()
      ) : (
        <>
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
              <span className={`job-type-badge mode-${runMode(job)}`}>
                {runModeIcon(runMode(job))}
                {runModeLabel(runMode(job))}
              </span>
              {runMode(job) === 'scheduled' && (
                <span className="job-schedule-label">{scheduleLabel(job.schedule)}</span>
              )}
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
          {selectedJobId === 'reddit_scraper' && jobDetail.monitor ? renderScraperDetail() : renderGenericDetail()}
          {renderFetchQueue()}
          {renderNerQueue()}
          {renderRelevanceQueue()}

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
        </>
      )}
    </div>
  )
}
