import { useState, useEffect, useCallback, useRef } from 'react'
import { FiX, FiArrowUp, FiMessageSquare, FiExternalLink, FiSend, FiLoader, FiCheck, FiChevronUp, FiChevronDown } from 'react-icons/fi'
import ReactMarkdown from 'react-markdown'
import { get, post } from '../api/client'
import './LLMAnalysisModal.css'

const DEFAULT_SYSTEM_PROMPT = `You are a financial analyst reviewing a spike in Reddit discussion about a specific stock ticker. Your job is to extract the densest, most actionable signal from these posts — not to summarize them.

This is likely a spike in activity. Identify what drove it.

Format:
## Catalyst
What event or news drove the spike? Be specific — earnings beat/miss, product launch, regulatory decision, analyst upgrade/downgrade, macro event, or an emerging thesis (e.g. "datacenter memory demand outpacing supply"). One to three sentences max.

## Bull Case
The strongest arguments for the stock, stated as terse bullets. Hard numbers only (revenue, EPS, guidance, price targets). Attribute to u/username. No folklore, no personal gain stories, no meme narratives.

## Bear Case
The strongest arguments against. Same format.

## Key Data Points
Hard numbers cited across all posts — analyst targets, financial results, percentages, ratios. Bullet list with u/username attribution.

## Risk Factors
What could go wrong. Bullets.

## Sentiment
One line. Bullish / bearish / mixed. Brief why.

Rules:
- Every sentence must contain information. Cut all filler, narrative padding, recaps, and transitions.
- State the content of posts directly. Do not say "users discussed X" or "posts mentioned Y" — just say X or Y.
- Do not include personal trading stories, legendary posters, or community folklore.
- Cite u/username for each non-obvious claim.
- Use bullet points. Avoid paragraphs.`

function tsToDateStr(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toISOString().slice(0, 10)
}

function dateStrToTsStart(str) {
  if (!str) return null
  const [y, m, d] = str.split('-').map(Number)
  return new Date(y, m - 1, d).getTime() / 1000
}

function dateStrToTsEnd(str) {
  if (!str) return null
  const [y, m, d] = str.split('-').map(Number)
  return new Date(y, m - 1, d, 23, 59, 59).getTime() / 1000
}

function formatRelativeTime(epoch) {
  if (!epoch) return ''
  const diff = Date.now() / 1000 - epoch
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

const SUB_COLORS = [
  '99, 102, 241', '244, 114, 182', '52, 211, 153', '251, 191, 36',
  '96, 165, 250', '167, 139, 250', '248, 113, 113', '45, 212, 191',
  '253, 186, 116', '156, 163, 175',
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

export default function LLMAnalysisModal({ isOpen, onClose, ticker, prefillDateFrom, prefillDateTo }) {
  const [models, setModels] = useState([])
  const [selectedModel, setSelectedModel] = useState('anthropic/claude-sonnet-4')
  const [systemPrompt, setSystemPrompt] = useState(DEFAULT_SYSTEM_PROMPT)
  const [dateFrom, setDateFrom] = useState(prefillDateFrom ? tsToDateStr(prefillDateFrom) : '')
  const [dateTo, setDateTo] = useState(prefillDateTo ? tsToDateStr(prefillDateTo) : '')

  const [staged, setStaged] = useState(null)
  const [staging, setStaging] = useState(false)
  const [stageError, setStageError] = useState(null)
  const [excludedIds, setExcludedIds] = useState(new Set())

  const [streaming, setStreaming] = useState(false)
  const [streamContent, setStreamContent] = useState('')
  const [streamError, setStreamError] = useState(null)
  const [savedId, setSavedId] = useState(null)
  const [toolsEnabled, setToolsEnabled] = useState(false)
  const [toolActivity, setToolActivity] = useState([])
  const [promptCollapsed, setPromptCollapsed] = useState(false)
  const streamRef = useRef(null)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)

  const handleClose = useCallback(() => {
    if (streaming) {
      setShowCloseConfirm(true)
      return
    }
    onClose()
  }, [streaming, onClose])

  useEffect(() => {
    if (!isOpen) {
      setShowCloseConfirm(false)
      return
    }
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (showCloseConfirm) {
          setShowCloseConfirm(false)
        } else {
          handleClose()
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [isOpen, showCloseConfirm, handleClose])

  useEffect(() => {
    if (isOpen) {
      get('/analysis/models')
        .then((res) => setModels(res.models))
        .catch(() => {})
      setDateFrom(prefillDateFrom ? tsToDateStr(prefillDateFrom) : '')
      setDateTo(prefillDateTo ? tsToDateStr(prefillDateTo) : '')
      setStaged(null)
      setExcludedIds(new Set())
      setStreamContent('')
      setStreamError(null)
      setSavedId(null)
      setToolActivity([])
    }
  }, [isOpen, prefillDateFrom, prefillDateTo])

  const stagePosts = useCallback(async () => {
    if (!dateFrom || !dateTo) return
    setStaging(true)
    setStageError(null)
    setExcludedIds(new Set())
    try {
      const res = await post('/analysis/stage', {
        ticker,
        date_from: String(dateStrToTsStart(dateFrom)),
        date_to: String(dateStrToTsEnd(dateTo)),
      })
      setStaged(res)
    } catch (err) {
      setStageError(err.message)
    } finally {
      setStaging(false)
    }
  }, [ticker, dateFrom, dateTo])

  const toggleExclude = (id) => {
    setExcludedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const activePosts = staged?.staged.filter((s) => !excludedIds.has(s.id)) || []
  const activeTokenEst = staged
    ? Math.round(activePosts.reduce((sum, s) => sum + s.word_count + s.title.split(' ').length, 0) * 1.3)
    : 0

  const runAnalysis = useCallback(async () => {
    if (activePosts.length === 0) return
    setStreaming(true)
    setStreamContent('')
    setStreamError(null)
    setSavedId(null)
    setToolActivity([])

    try {
      const response = await fetch('/api/analysis/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker,
          model: selectedModel,
          system_prompt: systemPrompt,
          posts: activePosts,
          tools_enabled: toolsEnabled,
          date_from: dateFrom,
          date_to: dateTo,
        }),
      })

      if (!response.ok) {
        const text = await response.text()
        setStreamError(`${response.status} ${text}`)
        setStreaming(false)
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const text = decoder.decode(value, { stream: true })
        const lines = text.split('\n')
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const dataStr = line.slice(6)
          try {
            const chunk = JSON.parse(dataStr)
            if (chunk.content) {
              setStreamContent((prev) => prev + chunk.content)
            }
            if (chunk.error) {
              setStreamError(chunk.error)
            }
            if (chunk.tool_activity) {
              setToolActivity((prev) => [...prev, chunk.tool_activity])
            }
            if (chunk.done) {
              setSavedId(chunk.analysis_id)
            }
          } catch {
            // partial line, ignore
          }
        }
      }
    } catch (err) {
      setStreamError(err.message)
    } finally {
      setStreaming(false)
    }
  }, [activePosts, ticker, selectedModel, systemPrompt, toolsEnabled, dateFrom, dateTo])

  if (!isOpen) return null

  return (
    <div className="llm-modal-backdrop">
      <div className="llm-modal">
        <div className="llm-modal-header">
          <h2>LLM Analysis — {ticker}</h2>
          <button className="llm-modal-close" onClick={handleClose}>
            <FiX />
          </button>
        </div>
        {showCloseConfirm && (
          <div className="llm-close-confirm">
            <span>Analysis is still streaming. Close anyway?</span>
            <button className="llm-close-confirm-btn" onClick={() => { setShowCloseConfirm(false); onClose() }}>Yes, close</button>
            <button className="llm-close-confirm-btn cancel" onClick={() => setShowCloseConfirm(false)}>Cancel</button>
          </div>
        )}

        <div className="llm-modal-body">
          {/* ── Left: Config + Staging ────────────────────────── */}
          <div className="llm-left-panel">
            {/* Config Section */}
            <div className="llm-config-section">
              <div className="llm-config-row">
                <div className="llm-config-group">
                  <label className="llm-config-label">Model</label>
                  <select
                    className="llm-model-select"
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>{m.label}</option>
                    ))}
                  </select>
                </div>
                <div className="llm-config-group">
                  <label className="llm-config-label">Date From</label>
                  <input
                    type="date"
                    className="llm-date-input"
                    value={dateFrom}
                    onChange={(e) => setDateFrom(e.target.value)}
                  />
                </div>
                <div className="llm-config-group">
                  <label className="llm-config-label">Date To</label>
                  <input
                    type="date"
                    className="llm-date-input"
                    value={dateTo}
                    onChange={(e) => setDateTo(e.target.value)}
                  />
                </div>
                <button
                  className="llm-stage-btn"
                  onClick={stagePosts}
                  disabled={!dateFrom || !dateTo || staging}
                >
                  {staging ? <FiLoader className="spin" /> : 'Stage Posts'}
                </button>
              </div>

              <div className="llm-config-group llm-prompt-group">
                <div className="llm-prompt-header" onClick={() => setPromptCollapsed(!promptCollapsed)}>
                  <label className="llm-config-label">System Prompt</label>
                  <button className="llm-prompt-toggle" type="button">
                    {promptCollapsed ? <FiChevronDown /> : <FiChevronUp />}
                  </button>
                </div>
                {!promptCollapsed && (
                  <textarea
                    className="llm-system-prompt"
                    value={systemPrompt}
                    onChange={(e) => setSystemPrompt(e.target.value)}
                    rows={10}
                  />
                )}
              </div>

              <div className="llm-tools-toggle">
                <label>
                  <input
                    type="checkbox"
                    checked={toolsEnabled}
                    onChange={(e) => setToolsEnabled(e.target.checked)}
                  />
                  Enable Analysis Tools
                </label>
                <span className="llm-tools-tooltip">
                  Event tracking + web search. Off by default to keep historical tests clean.
                </span>
              </div>
            </div>

            {stageError && (
              <div className="llm-error">{stageError}</div>
            )}

            {/* Staged Posts Section */}
            {staged && (
              <div className="llm-staged-section">
                <div className="llm-staged-header">
                  <span className="llm-staged-title">
                    Staged ({activePosts.length}/{staged.count})
                  </span>
                  <span className="llm-token-est">
                    ~<strong>{activeTokenEst.toLocaleString()}</strong> tokens
                  </span>
                </div>

                <div className="llm-staged-list">
                  {staged.staged.map((s) => {
                    const color = subColor(s.subreddit)
                    const isExcluded = excludedIds.has(s.id)
                    return (
                      <div
                        key={s.id}
                        className={`llm-staged-item ${isExcluded ? 'excluded' : ''}`}
                      >
                        <label className="llm-staged-check">
                          <input
                            type="checkbox"
                            checked={!isExcluded}
                            onChange={() => toggleExclude(s.id)}
                          />
                        </label>
                        <div className="llm-staged-content">
                          <div className="llm-staged-title-row">
                            <span className={`llm-staged-type ${s.type}`}>
                              {s.type}
                            </span>
                            {s.title && <span className="llm-staged-post-title">{s.title}</span>}
                          </div>
                          <p className="llm-staged-preview">
                            {s.body.slice(0, 200)}{s.body.length > 200 ? '...' : ''}
                          </p>
                          <div className="llm-staged-meta">
                            <span
                              className="llm-staged-sub"
                              style={{ background: `rgba(${color}, 0.15)`, color: `rgb(${color})` }}
                            >
                              r/{s.subreddit}
                            </span>
                            <span className="llm-staged-author">u/{s.author}</span>
                            <span className="llm-staged-stat">
                              <FiArrowUp /> {s.score}
                            </span>
                            {s.num_comments != null && (
                              <span className="llm-staged-stat">
                                <FiMessageSquare /> {s.num_comments}
                              </span>
                            )}
                            <span className="llm-staged-time">
                              {formatRelativeTime(s.created_utc)}
                              <span className="llm-staged-time-utc">{formatUTC(s.created_utc)}</span>
                            </span>
                            {s.is_truncated && (
                              <span className="llm-staged-truncated">
                                {s.word_count}/{s.orig_word_count} words
                              </span>
                            )}
                            <a
                              href={s.reddit_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="llm-staged-ext"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <FiExternalLink />
                            </a>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>

                <button
                  className="llm-run-btn"
                  onClick={runAnalysis}
                  disabled={activePosts.length === 0 || streaming}
                >
                  {streaming ? <FiLoader className="spin" /> : <FiSend />}
                  {streaming ? 'Streaming...' : `Run Analysis (${activePosts.length} posts)`}
                </button>
              </div>
            )}
          </div>

          {/* ── Right: Stream Output ───────────────────────────── */}
          <div className="llm-right-panel">
            <div className="llm-stream-section">
              <div className="llm-stream-header">
                <span className="llm-stream-title">Analysis Output</span>
                {savedId && (
                  <span className="llm-saved-badge">
                    <FiCheck /> Saved (#{savedId})
                  </span>
                )}
              </div>
              {streamError && (
                <div className="llm-error">{streamError}</div>
              )}
              {streamContent && (
                <div className="llm-stream-output" ref={streamRef}>
                  <ReactMarkdown>{streamContent}</ReactMarkdown>
                  {streaming && <span className="llm-cursor" />}
                </div>
              )}
              {!streamContent && !streamError && (
                <div className="llm-stream-placeholder">
                  Stage posts and run analysis to see results here.
                </div>
              )}
              {toolActivity.length > 0 && (
                <div className="llm-tool-activity">
                  {toolActivity.map((a, i) => (
                    <div key={i} className="llm-tool-activity-line">
                      <span className={`badge ${a.type}`}>{a.type}</span>
                      <span>{a.message}</span>
                    </div>
                  ))}
                </div>
              )}
              {savedId && toolActivity.length > 0 && (() => {
                const created = toolActivity.filter((a) => a.type === 'create').length
                const updated = toolActivity.filter((a) => a.type === 'update').length
                const resolved = toolActivity.filter((a) => a.type === 'resolve').length
                const searched = toolActivity.filter((a) => a.type === 'web').length
                return (
                  <div className="llm-tool-summary">
                    <span>Created {created}, Updated {updated}, Resolved {resolved}, {searched} web searches</span>
                    <a onClick={() => window.location.hash = '#/events'}>View Events →</a>
                  </div>
                )
              })()}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}