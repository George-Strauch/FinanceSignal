import { useRef, useEffect, useState } from 'react'
import { FiFilter } from 'react-icons/fi'
import './TagFilterButton.css'

export default function TagFilterButton({ tagSets, hiddenTagIds, onToggleTag, onClearTags }) {
  const [filterOpen, setFilterOpen] = useState(false)
  const filterRef = useRef(null)

  useEffect(() => {
    const handleClick = (e) => {
      if (filterRef.current && !filterRef.current.contains(e.target)) setFilterOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  if (!tagSets || tagSets.length === 0) return null

  return (
    <div className="tag-filter-wrap" ref={filterRef}>
      <button
        className={`toggle-btn tag-filter-btn ${hiddenTagIds.length > 0 ? 'active' : ''}`}
        onClick={() => setFilterOpen((v) => !v)}
        title="Exclude tickers by tag"
      >
        <FiFilter />
        {hiddenTagIds.length > 0 && <span className="tag-filter-count">{hiddenTagIds.length}</span>}
      </button>
      {filterOpen && (
        <div className="tag-filter-dropdown">
          <div className="tag-filter-title">Exclude tickers tagged as:</div>
          {tagSets.map((ts) => (
            <label key={ts.id} className="tag-filter-item">
              <input
                type="checkbox"
                checked={hiddenTagIds.includes(ts.id)}
                onChange={() => onToggleTag(ts.id)}
              />
              <span className="tag-filter-swatch" style={{ backgroundColor: ts.color }} />
              <span>{ts.name}</span>
              {ts.tickers?.length != null && (
                <span className="tag-filter-ticker-count">{ts.tickers.length}</span>
              )}
            </label>
          ))}
          {hiddenTagIds.length > 0 && (
            <button className="tag-filter-clear" onClick={onClearTags}>
              Clear filters
            </button>
          )}
        </div>
      )}
    </div>
  )
}