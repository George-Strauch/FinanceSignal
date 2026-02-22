import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiMessageSquare } from 'react-icons/fi'
import { get } from '../api/client'
import './Sources.css'

const SOURCE_CARDS = [
  {
    id: 'reddit',
    name: 'Reddit',
    icon: FiMessageSquare,
    description: 'Financial subreddits — posts, comments, and sentiment',
    route: '/sources/reddit',
  },
]

export default function Sources() {
  const navigate = useNavigate()
  const [redditStats, setRedditStats] = useState(null)

  useEffect(() => {
    get('/subreddits')
      .then((data) => setRedditStats(data))
      .catch(() => {})
  }, [])

  const statsFor = (id) => {
    if (id === 'reddit' && redditStats) {
      const count = redditStats.subreddits?.length ?? 0
      const posts = redditStats.subreddits?.reduce((s, r) => s + r.post_count, 0) ?? 0
      return { subreddits: count, posts }
    }
    return null
  }

  return (
    <div className="sources-page">
      <h1>Data Sources</h1>

      <div className="sources-grid">
        {SOURCE_CARDS.map(({ id, name, icon: Icon, description, route }) => {
          const stats = statsFor(id)
          return (
            <div
              key={id}
              className="dash-card source-card"
              onClick={() => navigate(route)}
            >
              <div className="source-card-header">
                <Icon className="source-card-icon" />
                <div>
                  <h2 className="source-card-name">{name}</h2>
                  <p className="source-card-desc">{description}</p>
                </div>
              </div>

              {stats && (
                <div className="source-card-stats">
                  <span>{stats.subreddits} subreddits</span>
                  <span className="source-card-dot" />
                  <span>{stats.posts.toLocaleString()} posts</span>
                </div>
              )}

              <div className="source-card-footer">
                <span className="source-status-badge active">Active</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
