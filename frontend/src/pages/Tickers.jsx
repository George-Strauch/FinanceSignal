import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FiSearch, FiClock, FiChevronLeft, FiChevronRight } from 'react-icons/fi'
import { get } from '../api/client'
import TagFilterButton from '../components/TagFilterButton'
import './Tickers.css'

const RECENT_KEY = 'recent-ticker-visits'
const MAX_RECENT = 25
const PAGE_SIZE = 50

function getRecentTickers() {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
  } catch {
    return []
  }
}

export function recordTickerVisit(ticker) {
  const recent = getRecentTickers().filter((t) => t !== ticker)
  recent.unshift(ticker)
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)))
}

export default function Tickers() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [searchLoading, setSearchLoading] = useState(false)
  const [recentTickers, setRecentTickers] = useState([])

  const [directory, setDirectory] = useState(null)
  const [dirLoading, setDirLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [sortKey, setSortKey] = useState('total_mentions')
  const [sortDir, setSortDir] = useState('desc')

  const [allTagSets, setAllTagSets] = useState([])
  const [hiddenTags, setHiddenTags] = useState([])

  useEffect(() => {
    setRecentTickers(getRecentTickers())
  }, [])

  useEffect(() => {
    get('/ticker-tags').then((res) => setAllTagSets(res.tag_sets)).catch(() => {})
  }, [])

  const fetchDirectory = useCallback(async () => {
    setDirLoading(true)
    try {
      const params = new URLSearchParams({
        page: String(page),
        limit: String(PAGE_SIZE),
        sort: sortKey,
        order: sortDir,
      })
      if (query.trim()) params.set('q', query.trim().toUpperCase())
      const res = await get(`/tickers/directory?${params}`)
      setDirectory(res.tickers)
      setTotalCount(res.total_count)
    } catch {
      setDirectory([])
      setTotalCount(0)
    } finally {
      setDirLoading(false)
    }
  }, [page, sortKey, sortDir, query])

  useEffect(() => {
    if (!searchLoading) fetchDirectory()
  }, [fetchDirectory, searchLoading])

  const search = useCallback(async (q) => {
    if (!q.trim()) {
      setResults(null)
      return
    }
    setSearchLoading(true)
    try {
      const res = await get(`/tickers/search?q=${encodeURIComponent(q)}&limit=50`)
      setResults(res.results)
    } catch {
      setResults([])
    } finally {
      setSearchLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => search(query), 300)
    return () => clearTimeout(timer)
  }, [query, search])

  const goToTicker = (ticker) => {
    recordTickerVisit(ticker)
    navigate(`/tickers/${ticker}`)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && query.trim()) {
      const symbol = query.trim().toUpperCase()
      goToTicker(symbol)
    }
  }

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
    setPage(1)
  }

  const sortIndicator = (key) => {
    if (sortKey !== key) return ''
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC'
  }

  const toggleTag = (tagId) => {
    setHiddenTags((prev) =>
      prev.includes(tagId) ? prev.filter((id) => id !== tagId) : [...prev, tagId]
    )
  }

  const showingSearch = results !== null
  const totalPages = Math.ceil(totalCount / PAGE_SIZE)

  const filteredDirectory = directory
    ? directory.filter((t) => {
        if (hiddenTags.length === 0) return true
        return !t.tags?.some((tag) => hiddenTags.includes(tag.id))
      })
    : []

  const filteredResults = results
    ? results.filter((t) => {
        if (hiddenTags.length === 0) return true
        return !t.tags?.some((tag) => hiddenTags.includes(tag.id))
      })
    : null

  return (
    <div className="tickers-page">
      <div className="tickers-search-hero">
        <h1>Tickers</h1>
        <div className="tickers-search-wrap">
          <FiSearch className="tickers-search-icon" />
          <input
            type="text"
            className="tickers-search"
            placeholder="Search tickers... press Enter to go"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            autoFocus
          />
        </div>
      </div>

      {showingSearch && filteredResults?.length === 0 && (
        <p className="tickers-empty">No tickers matching "{query}"</p>
      )}

      {searchLoading && !results && <p className="tickers-loading">Searching...</p>}

      {showingSearch && filteredResults?.length > 0 && (
        <div className="tickers-results-list">
          {filteredResults.map((t) => (
            <div
              key={t.ticker}
              className="tickers-result-row"
              onClick={() => goToTicker(t.ticker)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && goToTicker(t.ticker)}
            >
              <span className="tickers-row-symbol">
                {t.ticker}
                {t.tags?.map((tag) => (
                  <span key={tag.id} className="tag-chip" style={{ backgroundColor: tag.color, marginLeft: 6 }}>
                    {tag.name}
                  </span>
                ))}
              </span>
              <span className="tickers-row-count">{t.mention_count.toLocaleString()} mentions</span>
            </div>
          ))}
        </div>
      )}

      {!showingSearch && (
        <>
          {recentTickers.length > 0 && (
            <>
              <div className="tickers-section-label">
                <FiClock /> Recently Visited
              </div>
              <div className="tickers-recent-grid">
                {recentTickers.map((ticker) => (
                  <div
                    key={ticker}
                    className="ticker-recent-card"
                    onClick={() => goToTicker(ticker)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && goToTicker(ticker)}
                  >
                    <span className="ticker-recent-symbol">{ticker}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          <div className="tickers-directory-header">
            <div className="tickers-section-label">
              All Tickers <span className="tickers-total-count">({totalCount.toLocaleString()})</span>
            </div>
            <TagFilterButton
              tagSets={allTagSets}
              hiddenTagIds={hiddenTags}
              onToggleTag={toggleTag}
              onClearTags={() => setHiddenTags([])}
            />
          </div>

          {dirLoading && <p className="tickers-loading">Loading tickers...</p>}

          {!dirLoading && filteredDirectory.length === 0 && (
            <div className="tickers-empty-state">
              <FiSearch className="tickers-empty-icon" />
              <p>{hiddenTags.length > 0 ? 'All tickers are filtered out.' : 'No tickers found.'}</p>
              {hiddenTags.length > 0 && (
                <button className="tickers-clear-btn" onClick={() => setHiddenTags([])}>Clear filters</button>
              )}
            </div>
          )}

          {!dirLoading && filteredDirectory.length > 0 && (
            <>
              <div className="tickers-table-wrap">
                <table className="tickers-dir-table">
                  <thead>
                    <tr>
                      <th className="sortable" onClick={() => handleSort('ticker')}>
                        Ticker{sortIndicator('ticker')}
                      </th>
                      <th>Name</th>
                      <th className="sortable num-col" onClick={() => handleSort('total_mentions')}>
                        Mentions{sortIndicator('total_mentions')}
                      </th>
                      <th className="sortable num-col" onClick={() => handleSort('last_mention')}>
                        Last Mention{sortIndicator('last_mention')}
                      </th>
                      <th>Sector</th>
                      <th>Tags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDirectory.map((t) => (
                      <tr
                        key={t.ticker}
                        onClick={() => goToTicker(t.ticker)}
                        className="clickable-row"
                      >
                        <td className="ticker-cell">{t.ticker}</td>
                        <td className="ticker-name-cell">{t.name || '-'}</td>
                        <td className="num-col">{t.total_mentions.toLocaleString()}</td>
                        <td className="num-col">
                          {t.last_mention ? new Date(t.last_mention).toLocaleDateString() : '-'}
                        </td>
                        <td>{t.sector || '-'}</td>
                        <td>
                          <div className="table-tags">
                            {t.tags?.map((tag) => (
                              <span key={tag.id} className="tag-chip" style={{ backgroundColor: tag.color }}>
                                {tag.name}
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="tickers-pagination">
                  <button
                    className="tickers-page-btn"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                  >
                    <FiChevronLeft />
                  </button>
                  <span className="tickers-page-info">
                    Page {page} of {totalPages}
                  </span>
                  <button
                    className="tickers-page-btn"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                  >
                    <FiChevronRight />
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}