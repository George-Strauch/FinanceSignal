import { useEffect, useState, useCallback } from 'react'
import { FiPlus, FiTrash2, FiX, FiEdit2, FiCheck } from 'react-icons/fi'
import { get, post, put, del } from '../api/client'
import './TickerTags.css'

export default function TickerTags() {
  const [tagSets, setTagSets] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [tickerInput, setTickerInput] = useState('')

  // Create form
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newColor, setNewColor] = useState('#6b7280')
  const [newDesc, setNewDesc] = useState('')

  // Inline edit
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const [editColor, setEditColor] = useState('')
  const [editDesc, setEditDesc] = useState('')

  const fetchTags = useCallback(async () => {
    setLoading(true)
    try {
      const res = await get('/ticker-tags')
      setTagSets(res.tag_sets)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchTags() }, [fetchTags])

  const handleCreate = async (e) => {
    e.preventDefault()
    if (!newName.trim()) return
    try {
      await post('/ticker-tags', { name: newName, color: newColor, description: newDesc })
      setNewName('')
      setNewColor('#6b7280')
      setNewDesc('')
      setShowCreate(false)
      fetchTags()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleDelete = async (id) => {
    if (!confirm(`Delete tag set "${id}"?`)) return
    try {
      await del(`/ticker-tags/${id}`)
      if (expandedId === id) setExpandedId(null)
      fetchTags()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleAddTicker = async (tagId) => {
    const tickers = tickerInput.split(/[\s,]+/).filter(Boolean)
    if (tickers.length === 0) return
    try {
      await post(`/ticker-tags/${tagId}/tickers`, { tickers })
      setTickerInput('')
      fetchTags()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleRemoveTicker = async (tagId, ticker) => {
    try {
      await del(`/ticker-tags/${tagId}/tickers/${ticker}`)
      fetchTags()
    } catch (err) {
      setError(err.message)
    }
  }

  const startEdit = (ts) => {
    setEditingId(ts.id)
    setEditName(ts.name)
    setEditColor(ts.color)
    setEditDesc(ts.description)
  }

  const handleSaveEdit = async (tagId) => {
    try {
      await put(`/ticker-tags/${tagId}`, {
        name: editName,
        color: editColor,
        description: editDesc,
      })
      setEditingId(null)
      fetchTags()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="ticker-tags-page">
      <div className="tt-header">
        <h1>Ticker Tags</h1>
        <button className="tt-create-btn" onClick={() => setShowCreate(!showCreate)}>
          <FiPlus /> New Tag Set
        </button>
      </div>

      {error && <div className="tt-error">{error}</div>}

      {showCreate && (
        <form className="tt-create-form" onSubmit={handleCreate}>
          <div className="tt-form-row">
            <input
              type="text"
              placeholder="Tag name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="tt-input"
              autoFocus
            />
            <input
              type="color"
              value={newColor}
              onChange={(e) => setNewColor(e.target.value)}
              className="tt-color-input"
            />
          </div>
          <input
            type="text"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            className="tt-input tt-input-full"
          />
          <div className="tt-form-actions">
            <button type="submit" className="tt-btn tt-btn-primary">Create</button>
            <button type="button" className="tt-btn" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </form>
      )}

      {loading && tagSets.length === 0 && <p className="tt-loading">Loading...</p>}

      <div className="tt-list">
        {tagSets.map((ts) => {
          const isExpanded = expandedId === ts.id
          const isEditing = editingId === ts.id

          return (
            <div key={ts.id} className={`tt-card ${isExpanded ? 'expanded' : ''}`} style={{ borderLeftColor: ts.color }}>
              <div className="tt-card-header" onClick={() => setExpandedId(isExpanded ? null : ts.id)}>
                <div className="tt-card-info">
                  {isEditing ? (
                    <div className="tt-edit-row" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="tt-input tt-input-sm"
                      />
                      <input
                        type="color"
                        value={editColor}
                        onChange={(e) => setEditColor(e.target.value)}
                        className="tt-color-input"
                      />
                      <input
                        type="text"
                        value={editDesc}
                        onChange={(e) => setEditDesc(e.target.value)}
                        className="tt-input tt-input-sm"
                        placeholder="Description"
                      />
                      <button className="tt-icon-btn" onClick={() => handleSaveEdit(ts.id)} title="Save">
                        <FiCheck />
                      </button>
                      <button className="tt-icon-btn" onClick={() => setEditingId(null)} title="Cancel">
                        <FiX />
                      </button>
                    </div>
                  ) : (
                    <>
                      <span className="tt-card-swatch" style={{ backgroundColor: ts.color }} />
                      <span className="tt-card-name">{ts.name}</span>
                      {ts.description && <span className="tt-card-desc">{ts.description}</span>}
                      <span className="tt-card-count">{ts.tickers.length} tickers</span>
                    </>
                  )}
                </div>
                {!isEditing && (
                  <div className="tt-card-actions" onClick={(e) => e.stopPropagation()}>
                    <button className="tt-icon-btn" onClick={() => startEdit(ts)} title="Edit">
                      <FiEdit2 />
                    </button>
                    <button className="tt-icon-btn tt-icon-btn-danger" onClick={() => handleDelete(ts.id)} title="Delete">
                      <FiTrash2 />
                    </button>
                  </div>
                )}
              </div>

              {isExpanded && (
                <div className="tt-card-body">
                  <div className="tt-ticker-chips">
                    {ts.tickers.map((ticker) => (
                      <span key={ticker} className="tt-ticker-chip">
                        {ticker}
                        <button
                          className="tt-chip-remove"
                          onClick={() => handleRemoveTicker(ts.id, ticker)}
                          title={`Remove ${ticker}`}
                        >
                          <FiX />
                        </button>
                      </span>
                    ))}
                    {ts.tickers.length === 0 && (
                      <span className="tt-empty-tickers">No tickers yet</span>
                    )}
                  </div>
                  <div className="tt-add-ticker-row">
                    <input
                      type="text"
                      placeholder="Add tickers (comma-separated)"
                      value={tickerInput}
                      onChange={(e) => setTickerInput(e.target.value)}
                      className="tt-input"
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          handleAddTicker(ts.id)
                        }
                      }}
                    />
                    <button className="tt-btn tt-btn-primary" onClick={() => handleAddTicker(ts.id)}>
                      <FiPlus /> Add
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
