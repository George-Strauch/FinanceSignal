import { useEffect, useState, useCallback } from 'react'
import { FiChevronLeft, FiChevronRight, FiAlertCircle, FiRefreshCw } from 'react-icons/fi'
import { get } from '../api/client'
import PostCard from './PostCard'
import './PostFeed.css'

const SORT_LABELS = {
  date: 'Newest',
  score: 'Top',
  comments: 'Most Comments',
}

export default function PostFeed({
  ticker,
  subreddit,
  perPage = 25,
  highlightTicker,
  title = 'Recent Posts',
}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('date')

  const fetchPosts = useCallback(async () => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (ticker) params.set('ticker', ticker)
    if (subreddit) params.set('subreddit', subreddit)
    params.set('page', page)
    params.set('per_page', perPage)
    params.set('sort', sort)
    try {
      const res = await get(`/posts?${params}`)
      setData(res)
    } catch (err) {
      setError(err.message)
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [ticker, subreddit, page, perPage, sort])

  useEffect(() => { fetchPosts() }, [fetchPosts])
  useEffect(() => { setPage(1) }, [sort, ticker, subreddit])

  const pagination = data?.pagination

  return (
    <div className="post-feed">
      <div className="post-feed-header">
        <h2 className="post-feed-title">{title}</h2>
        <div className="post-feed-sort">
          {Object.entries(SORT_LABELS).map(([key, label]) => (
            <button
              key={key}
              className={`pf-sort-btn ${sort === key ? 'active' : ''}`}
              onClick={() => setSort(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Loading skeleton */}
      {loading && !data && (
        <div className="post-feed-skeleton">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="pf-skel-card">
              <div className="pf-skel-line pf-skel-title" />
              <div className="pf-skel-line pf-skel-preview" />
              <div className="pf-skel-line pf-skel-meta" />
            </div>
          ))}
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="post-feed-error">
          <FiAlertCircle className="pf-error-icon" />
          <span>Failed to load posts: {error}</span>
          <button className="pf-retry-btn" onClick={fetchPosts}>
            <FiRefreshCw /> Retry
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && data?.posts?.length === 0 && (
        <p className="post-feed-empty">No posts found{ticker ? ` mentioning ${ticker.toUpperCase()}` : ''}.</p>
      )}

      {/* Post list */}
      {data?.posts?.length > 0 && (
        <>
          <div className={`post-feed-list ${loading ? 'pf-refetching' : ''}`}>
            {data.posts.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                highlightTicker={highlightTicker || ticker}
              />
            ))}
          </div>

          {pagination && pagination.total_pages > 1 && (
            <div className="post-feed-pagination">
              <button
                className="pf-page-btn"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                <FiChevronLeft />
              </button>
              <span className="pf-page-info">
                Page {pagination.page} of {pagination.total_pages}
                <span className="pf-total-posts"> ({pagination.total_posts} posts)</span>
              </span>
              <button
                className="pf-page-btn"
                disabled={page >= pagination.total_pages}
                onClick={() => setPage((p) => p + 1)}
              >
                <FiChevronRight />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
