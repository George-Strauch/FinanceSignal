import { Link } from 'react-router-dom'
import { FiExternalLink, FiArrowUp, FiMessageSquare, FiTarget } from 'react-icons/fi'

const SUB_COLORS = [
  '99, 102, 241',
  '244, 114, 182',
  '52, 211, 153',
  '251, 191, 36',
  '96, 165, 250',
  '167, 139, 250',
  '248, 113, 113',
  '45, 212, 191',
  '253, 186, 116',
  '156, 163, 175',
]

function hashCode(str) {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0
  }
  return Math.abs(hash)
}

function subColor(subreddit) {
  return SUB_COLORS[hashCode(subreddit) % SUB_COLORS.length]
}

function formatRelativeTime(epoch) {
  if (!epoch) return ''
  const diff = Date.now() / 1000 - epoch
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function formatUTC(epoch) {
  if (!epoch) return ''
  const d = new Date(epoch * 1000)
  const y = d.getUTCFullYear()
  const m = String(d.getUTCMonth() + 1).padStart(2, '0')
  const day = String(d.getUTCDate()).padStart(2, '0')
  const h = String(d.getUTCHours()).padStart(2, '0')
  const min = String(d.getUTCMinutes()).padStart(2, '0')
  return `${y}-${m}-${day} ${h}:${min} UTC`
}

function scoreClass(score) {
  if (score == null) return ''
  if (score >= 0.7) return 'rel-high'
  if (score >= 0.4) return 'rel-mid'
  return 'rel-low'
}

function fmtScore(score) {
  if (score == null) return ''
  return score.toFixed(1)
}

export default function PostCard({ post, highlightTicker }) {
  const color = subColor(post.subreddit)

  return (
    <div className="post-card">
      <div className="post-card-title-row">
        <a
          href={post.reddit_url}
          target="_blank"
          rel="noopener noreferrer"
          className="post-card-title"
          onClick={(e) => e.stopPropagation()}
          title="Open on Reddit"
        >
          {post.title}
        </a>
        <a
          href={post.reddit_url}
          target="_blank"
          rel="noopener noreferrer"
          className="post-card-ext-link"
          onClick={(e) => e.stopPropagation()}
          title="Open on Reddit"
        >
          <FiExternalLink />
        </a>
      </div>

      {post.selftext_preview && (
        <p className="post-card-preview">{post.selftext_preview}</p>
      )}

      <div className="post-card-meta">
        <span
          className="post-card-sub"
          style={{ background: `rgba(${color}, 0.15)`, color: `rgb(${color})` }}
        >
          r/{post.subreddit}
        </span>
        {post.sentiment_label && post.sentiment_label !== 'neutral' && (
          <span className={`post-card-sentiment-dot sentiment-dot-${post.sentiment_label}`} title={post.sentiment_label} />
        )}
        {post.author && post.author !== '[deleted]' ? (
          <Link to={`/authors/${post.author}`} className="post-card-author post-card-author-link" onClick={(e) => e.stopPropagation()}>
            u/{post.author}
          </Link>
        ) : (
          <span className="post-card-author">u/{post.author}</span>
        )}
        <span className="post-card-stat">
          <FiArrowUp /> {post.score}
        </span>
        <span className="post-card-stat">
          <FiMessageSquare /> {post.num_comments}
        </span>
        <span className="post-card-time">
          {formatRelativeTime(post.created_utc)}
          <span className="post-card-time-utc">{formatUTC(post.created_utc)}</span>
        </span>
      </div>

      {(post.tickers_mentioned?.length > 0 || post.entities_mentioned?.length > 0) && (
        <div className="post-card-tags">
          {post.tickers_mentioned?.map((t) => {
            const score = post.ticker_scores?.[t]
            return (
              <Link
                key={`tk-${t}`}
                to={`/tickers/${t}`}
                className={`post-card-ticker-chip ${t === highlightTicker?.toUpperCase() ? 'current' : ''} ${scoreClass(score)}`}
                onClick={(e) => e.stopPropagation()}
                title={score != null ? `Relevance: ${score.toFixed(3)}` : undefined}
              >
                {t}
                {score != null && <span className="chip-score">{fmtScore(score)}</span>}
              </Link>
            )
          })}
          {post.entities_mentioned?.map((e) => {
            const score = post.entity_scores?.[e]
            return (
              <Link
                key={`ne-${e}`}
                to={`/entities/${encodeURIComponent(e)}`}
                className={`post-card-entity-chip ${scoreClass(score)}`}
                onClick={(ev) => ev.stopPropagation()}
                title={score != null ? `Relevance: ${score.toFixed(3)}` : undefined}
              >
                {e}
                {score != null && <span className="chip-score">{fmtScore(score)}</span>}
              </Link>
            )
          })}
        </div>
      )}

      {post.relevance_score != null && (
        <div className={`post-card-relevance-badge ${scoreClass(post.relevance_score)}`}>
          <FiTarget />
          <span>{(post.relevance_score * 100).toFixed(0)}% relevant</span>
        </div>
      )}
    </div>
  )
}