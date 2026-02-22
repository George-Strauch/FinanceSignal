import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { get } from '../api/client'
import './SystemStatus.css'

function formatUptime(seconds) {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

export default function SystemStatus() {
  const [health, setHealth] = useState(null)
  const [config, setConfig] = useState(null)
  const [error, setError] = useState(false)

  const fetchHealth = useCallback(async () => {
    try {
      const data = await get('/health')
      setHealth(data)
      setError(false)
    } catch {
      setHealth(null)
      setError(true)
    }
  }, [])

  const fetchConfig = useCallback(async () => {
    try {
      const data = await get('/config')
      setConfig(data)
    } catch {
      /* config fetch failure is non-critical */
    }
  }, [])

  useEffect(() => {
    fetchHealth()
    fetchConfig()
    const id = setInterval(fetchHealth, 30_000)
    return () => clearInterval(id)
  }, [fetchHealth, fetchConfig])

  const status = error ? 'disconnected' : health ? 'connected' : 'loading'

  return (
    <div className="system-status">
      {/* Health */}
      <div className="dash-card">
        <h2>Backend Status</h2>
        <div className="health-row">
          <span className={`status-dot ${status}`} />
          <span className={`status-label ${status}`}>
            {status === 'connected' && 'Connected'}
            {status === 'disconnected' && 'Disconnected'}
            {status === 'loading' && 'Checking\u2026'}
          </span>
        </div>
        {health && (
          <p className="uptime">Uptime: {formatUptime(health.uptime_seconds)}</p>
        )}
      </div>

      {/* Database Stats */}
      <div className="dash-card">
        <h2>Database</h2>
        {config ? (
          <div className="stats-grid">
            <div className="stat-item">
              <div className="stat-value">{config.post_count.toLocaleString()}</div>
              <div className="stat-label">Posts</div>
            </div>
            <div className="stat-item">
              <div className="stat-value">{config.comment_count.toLocaleString()}</div>
              <div className="stat-label">Comments</div>
            </div>
            <div className="stat-item">
              <div className="stat-value">{config.ticker_mention_count.toLocaleString()}</div>
              <div className="stat-label">Ticker Mentions</div>
            </div>
          </div>
        ) : (
          <p className="dash-placeholder">{error ? 'Unavailable' : 'Loading\u2026'}</p>
        )}
      </div>

      {/* Data Sources */}
      <div className="dash-card">
        <h2>Data Sources</h2>
        {config ? (
          <div className="data-sources-summary">
            <div className="data-source-row">
              <span className="data-source-name">Reddit</span>
              <span className="data-source-detail">
                {config.subreddits?.length ?? 0} subreddits
              </span>
              <span className="source-status-dot active" />
            </div>
            <Link to="/sources" className="data-sources-link">Manage sources</Link>
          </div>
        ) : (
          <p className="dash-placeholder">{error ? 'Unavailable' : 'Loading\u2026'}</p>
        )}
      </div>
    </div>
  )
}
