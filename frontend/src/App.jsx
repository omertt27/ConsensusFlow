import { useState, useRef, useCallback, useEffect } from 'react'
import { clsx } from 'clsx'
import {
  CheckCircle2, AlertTriangle, XCircle, Zap, Clock,
  ChevronRight, RefreshCw, Copy, Check,
  Cpu, ShieldCheck, GitMerge, BarChart3, Sparkles,
  TrendingUp, Timer, Hash, History, Trash2, ExternalLink,
  Layers, Search, Database,
} from 'lucide-react'
import './App.css'

// ── Constants ─────────────────────────────────────────────────────────────────

const EXAMPLES = [
  'What time does Topkapi Palace open and how much does it cost?',
  'Is the Hagia Sophia a mosque or a museum in 2026?',
  'Who invented the telephone — Bell or Meucci?',
  'What year did the Berlin Wall fall and why?',
  'Is Mount Everest the tallest mountain on Earth by every measure?',
  'When was the Eiffel Tower built and how tall is it exactly?',
]

const PIPELINE_STEPS = [
  { key: 'proposer', icon: Cpu, label: 'Proposer' },
  { key: 'extractor', icon: BarChart3, label: 'Extractor' },
  { key: 'auditor', icon: ShieldCheck, label: 'Auditor' },
  { key: 'resolver', icon: GitMerge, label: 'Resolver' },
]

const STATUS_STEP_MAP = {
  proposer: 0, claim: 1, extract: 1,
  auditor: 2, audit: 2, resolver: 3, resolv: 3,
}

const HISTORY_KEY = 'consensusflow:history'
const MAX_HISTORY = 50

// ── Utilities ─────────────────────────────────────────────────────────────────

function scoreColor(s) {
  if (s >= 80) return 'var(--green-bright)'
  if (s >= 55) return 'var(--yellow)'
  return 'var(--red)'
}
function scoreHalo(s) {
  if (s >= 80) return 'var(--green-glow)'
  if (s >= 55) return 'var(--yellow-glow)'
  return 'var(--red-glow)'
}
function scoreLine(s) {
  if (s >= 80) return 'rgba(86, 211, 100, .5)'
  if (s >= 55) return 'rgba(227, 179, 65, .45)'
  return 'rgba(248, 81, 73, .5)'
}
function inferStep(statusText) {
  const t = (statusText || '').toLowerCase()
  for (const [k, v] of Object.entries(STATUS_STEP_MAP))
    if (t.includes(k)) return v
  return -1
}
function timeAgo(iso) {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso)) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

// ── localStorage History ───────────────────────────────────────────────────────

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') }
  catch { return [] }
}
function saveToHistory(report) {
  try {
    const history = loadHistory()
    const entry = {
      run_id: report.run_id,
      prompt: report.prompt,
      status: report.status,
      gotcha_score: report.gotcha_score?.score ?? null,
      total_tokens: report.total_tokens,
      total_latency_ms: report.total_latency_ms,
      created_at: report.created_at || new Date().toISOString(),
      chain_models: report.chain_models || [],
      _full: report,
    }
    const next = [entry, ...history.filter(h => h.run_id !== report.run_id)]
    localStorage.setItem(HISTORY_KEY, JSON.stringify(next.slice(0, MAX_HISTORY)))
  } catch (e) { console.warn('History save failed:', e) }
}
function clearHistory() { localStorage.removeItem(HISTORY_KEY) }

// ── History Panel ──────────────────────────────────────────────────────────────

function HistoryPanel({ onRestore }) {
  const [history, setHistory] = useState(loadHistory)
  const [search, setSearch] = useState('')

  const handleClear = () => { clearHistory(); setHistory([]) }

  const filtered = search.trim()
    ? history.filter(h => h.prompt.toLowerCase().includes(search.toLowerCase()))
    : history

  if (history.length === 0) return null

  return (
    <div className="history-panel">
      <div className="history-panel-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
          <div className="history-panel-title">
            <History size={13} />
            History
            <span className="history-count">{history.length}</span>
          </div>
          <div style={{ flex: 1, maxWidth: 240, position: 'relative' }}>
            <Search size={11} style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', pointerEvents: 'none' }} />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search history…"
              style={{ width: '100%', padding: '5px 10px 5px 26px', background: 'rgba(255,255,255,.04)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text2)', fontSize: '.75rem', outline: 'none' }}
            />
          </div>
        </div>
        <button className="history-clear-btn" onClick={handleClear}>
          <Trash2 size={10} style={{ marginRight: 3 }} />Clear all
        </button>
      </div>
      <div className="history-list">
        {filtered.length === 0 && <div className="history-empty">No results matching &quot;{search}&quot;</div>}
        {filtered.map(item => {
          const score = item.gotcha_score
          const color = score != null ? scoreColor(score) : 'var(--muted)'
          return (
            <div key={item.run_id} className="history-item" onClick={() => onRestore(item._full)} title="Click to restore this verification">
              <div className="history-item-score" style={{ color, borderColor: color, background: `${color}15` }}>
                {score ?? '?'}
              </div>
              <div className="history-item-body">
                <div className="history-item-prompt">{item.prompt}</div>
                <div className="history-item-meta">
                  <span className="history-meta-pill"><Clock size={9} />{timeAgo(item.created_at)}</span>
                  {item.total_tokens > 0 && <span className="history-meta-pill"><Hash size={9} />{item.total_tokens.toLocaleString()} tok</span>}
                  {item.total_latency_ms > 0 && <span className="history-meta-pill"><Timer size={9} />{(item.total_latency_ms / 1000).toFixed(1)}s</span>}
                  {item.chain_models?.length > 0 && <span className="history-meta-pill"><Layers size={9} />{item.chain_models[0]?.split('/').pop()}</span>}
                </div>
              </div>
              <div className="history-item-status" style={{ background: `${color}18`, color, border: `1px solid ${color}40` }}>
                {item.status}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}


// ── Main App Component ─────────────────────────────────────────────────────────

export default function App() {
  const [query, setQuery] = useState('')
  const [isStream, setStream] = useState(true)
  const [isLoading, setLoading] = useState(false)
  const [statusText, setStatusText] = useState('')
  const [activeStreamTab, setStreamTab] = useState('all')
  const [streamContent, setStreamContent] = useState({ all: '', proposer: '', auditor: '', resolver: '' })
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [charCount, setCharCount] = useState(0)
  const [copied, setCopied] = useState(false)
  const [cacheStats, setCacheStats] = useState(null)

  const streamAbortCtrl = useRef(null)

  const activePipelineStep = report ? 4 : inferStep(statusText)

  const fetchCacheStats = useCallback(async () => {
    try {
      const res = await fetch('/api/cache/stats')
      if (res.ok) setCacheStats(await res.json())
    } catch (e) { console.warn('Cache stats fetch failed', e) }
  }, [])

  const handleClearCache = useCallback(async () => {
    try {
      await fetch('/api/cache/clear', { method: 'POST' })
      fetchCacheStats()
    } catch (e) { console.warn('Cache clear failed', e) }
  }, [fetchCacheStats])

  useEffect(() => {
    fetchCacheStats()
    const interval = setInterval(fetchCacheStats, 15000)
    return () => clearInterval(interval)
  }, [fetchCacheStats])

  const handleRestore = (fullReport) => {
    setReport(fullReport)
    setQuery(fullReport.prompt)
    setCharCount(fullReport.prompt.length)
    setStreamContent({ all: '', proposer: '', auditor: '', resolver: '' })
    setStatusText('')
    setError(null)
    setLoading(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const handleVerify = useCallback(async () => {
    if (isLoading) {
      streamAbortCtrl.current?.abort()
      setLoading(false)
      setStatusText('Verification cancelled')
      return
    }

    setLoading(true)
    setReport(null)
    setError(null)
    setStatusText('Initiating verification…')
    setStreamContent({ all: '', proposer: '', auditor: '', resolver: '' })
    streamAbortCtrl.current = new AbortController()

    const endpoint = isStream ? '/api/verify/stream' : '/api/verify'
    const headers = { 'Content-Type': 'application/json' }
    const body = JSON.stringify({ prompt: query, enable_cache: true })

    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers,
        body,
        signal: streamAbortCtrl.current.signal,
      })

      if (!response.ok) {
        const errData = await response.json()
        throw new Error(errData.detail || `HTTP ${response.status}`)
      }

      if (isStream) {
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() // Keep partial line

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const jsonStr = line.substring(6)
              const eventData = JSON.parse(jsonStr)
              const { event, data } = eventData

              setStreamContent(prev => {
                let next = { ...prev }
                if (event.endsWith('_chunk')) {
                  const key = event.replace('_chunk', '')
                  if (key in next) next[key] += data
                  next.all += data
                }
                return next
              })

              if (event === 'status') setStatusText(data)
              if (event === 'done') {
                setReport(data)
                saveToHistory(data)
                fetchCacheStats()
              }
              if (event === 'error') setError(data)
            }
          }
        }
      } else { // Blocking request
        const finalReport = await response.json()
        setReport(finalReport)
        saveToHistory(finalReport)
        fetchCacheStats()
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError(err.message)
        setStatusText('Verification failed')
      }
    } finally {
      setLoading(false)
      if (!report) setStatusText(s => s === 'Verification cancelled' ? s : 'Done')
    }
  }, [query, isStream, isLoading, fetchCacheStats, report])

  const handleQueryChange = (e) => {
    setQuery(e.target.value)
    setCharCount(e.target.value.length)
  }

  const handleExampleClick = (ex) => {
    setQuery(ex)
    setCharCount(ex.length)
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(report?.final_answer || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="app">
      <div className="grid-background" />
      <div className="aurora">
        <div className="aurora-blob" />
        <div className="aurora-blob" />
        <div className="aurora-blob" />
      </div>

      <div className="main-content">
        <header className="header">
          <a href="/" className="logo">
            <div className="logo-icon"><Database size={18} /></div>
            <span className="logo-text">Consensus<span className="chromatic">Flow</span></span>
          </a>
          <div className="pipeline-header">
            {PIPELINE_STEPS.map((step, i) => (
              <>
                <div key={step.key} className={clsx('pipeline-step', activePipelineStep === i && 'active')}>
                  <step.icon size={14} />
                  <span>{step.label}</span>
                </div>
                {i < PIPELINE_STEPS.length - 1 && <ChevronRight size={16} className="pipeline-separator" />}
              </>
            ))}
          </div>
        </header>

        <div className="query-form">
          <div className="textarea-wrapper">
            <textarea
              className="query-textarea"
              placeholder="Enter a question or statement to verify..."
              value={query}
              onChange={handleQueryChange}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), handleVerify())}
              disabled={isLoading}
            />
            <div className="char-count">{charCount} / 8000</div>
          </div>

          <div className="form-controls">
            <div className="example-chips">
              {EXAMPLES.slice(0, 3).map(ex => (
                <button key={ex} className="chip" onClick={() => handleExampleClick(ex)} disabled={isLoading}>
                  {ex.split(' ').slice(0, 5).join(' ')}…
                </button>
              ))}
            </div>
            <div className="actions">
              <label className="stream-toggle" title="Toggle real-time streaming">
                <input type="checkbox" checked={isStream} onChange={() => setStream(!isStream)} disabled={isLoading} />
                <div className="toggle-switch" />
                <span>Stream</span>
              </label>
              <button className="verify-btn" onClick={handleVerify} disabled={!query.trim() || isLoading}>
                {isLoading ? <><Spinner /> Verifying...</> : <><Zap size={16} /> Verify</>}
              </button>
            </div>
          </div>
        </div>

        {(isLoading || statusText) && (
          <div className={clsx('status-bar', !isLoading && !report && statusText === 'Done' && 'hidden')}>
            {isLoading && <Spinner />}
            <span>{statusText}</span>
          </div>
        )}

        {isStream && isLoading && (
          <StreamPanel
            activeTab={activeStreamTab}
            onTabChange={setStreamTab}
            content={streamContent}
          />
        )}

        {report && (
          <>
            <div className="report-grid">
              <ScoreCard score={report.gotcha_score} />
              <StatsCard report={report} />
            </div>
            <AnswerCard report={report} onCopy={handleCopy} copied={copied} />
            <StepsGrid steps={report.steps} />
            <ClaimsCard claims={report.atomic_claims} />
          </>
        )}

        {error && <ErrorDisplay message={error} />}

        <HistoryPanel onRestore={handleRestore} />
      </div>

      {cacheStats && <CacheStats stats={cacheStats} onClear={handleClearCache} />}
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

const Spinner = () => <div className="spinner" />

const ErrorDisplay = ({ message }) => (
  <div className="error-display">
    <AlertTriangle />
    <strong>Error:</strong> {message}
  </div>
)

function StreamPanel({ activeTab, onTabChange, content }) {
  return (
    <div className="stream-panel">
      <div className="stream-tabs">
        <div className={clsx('stream-tab', activeTab === 'all')} onClick={() => onTabChange('all')}>All</div>
        <div className={clsx('stream-tab', activeTab === 'proposer')} onClick={() => onTabChange('proposer')}>Proposer</div>
        <div className={clsx('stream-tab', activeTab === 'auditor')} onClick={() => onTabChange('auditor')}>Auditor</div>
        <div className={clsx('stream-tab', activeTab === 'resolver')} onClick={() => onTabChange('resolver')}>Resolver</div>
      </div>
      <div className="stream-output">
        {content[activeTab]}
        <span className="cursor" />
      </div>
    </div>
  )
}

function ScoreCard({ score }) {
  if (!score) return null
  const color = scoreColor(score.score)
  const circumference = 2 * Math.PI * 84
  const offset = circumference - (score.score / 100) * circumference

  return (
    <div className="card score-card">
      <div className="score-ring-wrapper">
        <svg className="score-ring-svg" viewBox="0 0 180 180">
          <circle className="score-ring-bg" cx="90" cy="90" r="84" />
          <circle
            className="score-ring-fg"
            cx="90"
            cy="90"
            r="84"
            stroke={color}
            strokeDasharray={circumference}
            strokeDashoffset={offset}
          />
        </svg>
        <div className="score-text">
          <div className="score-value" style={{ color }}>
            {score.score}<sup>%</sup>
          </div>
          <div className="score-label">Gotcha Score</div>
        </div>
      </div>
      <div className="score-grade" style={{ color }}>{score.grade}</div>
      <div className="score-grade-label">{score.label}</div>
    </div>
  )
}

function StatsCard({ report }) {
  const savings = report.savings || { tokens_saved: 0, percent_saved: 0, saved_usd: 0 }
  return (
    <div className="card stats-card">
      <h3 className="card-title"><TrendingUp size={16} />Performance</h3>
      <div className="stats-grid">
        <div className="stat-item savings">
          <div className="stat-icon"><Sparkles size={18} /></div>
          <div className="stat-body">
            <div className="stat-value">{savings.percent_saved.toFixed(0)}%</div>
            <div className="stat-label">Efficiency Gain</div>
          </div>
        </div>
        <div className="stat-item tokens">
          <div className="stat-icon"><Hash size={18} /></div>
          <div className="stat-body">
            <div className="stat-value">{report.total_tokens.toLocaleString()}</div>
            <div className="stat-label">Total Tokens</div>
          </div>
        </div>
        <div className="stat-item latency">
          <div className="stat-icon"><Timer size={18} /></div>
          <div className="stat-body">
            <div className="stat-value">{(report.total_latency_ms / 1000).toFixed(2)}s</div>
            <div className="stat-label">Total Latency</div>
          </div>
        </div>
        <div className="stat-item cost">
          <div className="stat-icon">
            <span style={{ fontSize: '1.1rem', fontWeight: 600 }}>$</span>
          </div>
          <div className="stat-body">
            <div className="stat-value">{(report.total_cost_usd || 0).toFixed(4)}</div>
            <div className="stat-label">Estimated Cost</div>
          </div>
        </div>
      </div>
    </div>
  )
}

function AnswerCard({ report, onCopy, copied }) {
  return (
    <div className="card answer-card">
      <div className="answer-header">
        <div className={`answer-status ${report.status}`}>
          {report.status === 'SUCCESS' && <CheckCircle2 size={14} />}
          {report.status === 'EARLY_EXIT' && <Zap size={14} />}
          {report.status === 'PARTIAL' && <AlertTriangle size={14} />}
          {report.status === 'ERROR' && <XCircle size={14} />}
          {report.status.replace('_', ' ')}
        </div>
        <button className="copy-btn" onClick={onCopy}>
          {copied ? <Check size={16} /> : <Copy size={16} />}
        </button>
      </div>
      <div className="answer-text" dangerouslySetInnerHTML={{ __html: report.final_answer }} />
    </div>
  )
}

function StepsGrid({ steps }) {
  return (
    <div className="steps-grid">
      {steps.filter(Boolean).map(step => (
        <div key={step.step} className="step-card">
          <div className="step-header">
            <h4 className="step-title">
              {PIPELINE_STEPS.find(s => s.key.startsWith(step.step.slice(0, 4)))?.icon({ size: 16 })}
              {step.step.charAt(0).toUpperCase() + step.step.slice(1)}
            </h4>
            <div className="step-model">{step.model.split('/').pop()}</div>
          </div>
          <div className="step-stats">
            <div className="step-stat">
              <Hash size={12} />
              <span className="stat-value">{step.total_tokens.toLocaleString()}</span>
              <span>tok</span>
            </div>
            <div className="step-stat">
              <Timer size={12} />
              <span className="stat-value">{(step.latency_ms / 1000).toFixed(2)}</span>
              <span>s</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function ClaimsCard({ claims }) {
  const [filter, setFilter] = useState('all')
  const filteredClaims = claims.filter(c => {
    if (filter === 'all') return true
    if (filter === 'catches') return c.status !== 'VERIFIED'
    return c.status === filter
  })

  const filters = ['all', 'catches', 'VERIFIED', 'CORRECTED', 'REJECTED', 'DISPUTED', 'NUANCED']
  const catchCount = claims.filter(c => c.status !== 'VERIFIED').length

  return (
    <div className="card claims-card">
      <div className="claims-header">
        <h3 className="card-title"><Layers size={16} />Atomic Claims</h3>
        <div className="claims-filters">
          {filters.map(f => {
            const count = f === 'all' ? claims.length : f === 'catches' ? catchCount : claims.filter(c => c.status === f).length
            if (count === 0 && f !== 'all' && f !== 'catches') return null
            return (
              <button key={f} className={clsx('filter-btn', filter === f && 'active')} onClick={() => setFilter(f)}>
                {f === 'VERIFIED' && <CheckCircle2 size={12} />}
                {f === 'catches' && <AlertTriangle size={12} />}
                {f !== 'VERIFIED' && f !== 'catches' && f !== 'all' && <XCircle size={12} />}
                {f.replace('_', ' ')}
                <span className="filter-count">{count}</span>
              </button>
            )
          })}
        </div>
      </div>
      <div className="claims-list">
        {filteredClaims.map(claim => <ClaimItem key={claim.id} claim={claim} />)}
      </div>
    </div>
  )
}

function ClaimItem({ claim }) {
  const hasDetails = claim.note || claim.original_text || (claim.sources && claim.sources.length > 0)
  return (
    <div className="claim-item">
      <div className="claim-header">
        <p className="claim-text">{claim.text}</p>
        <div className={`claim-status-badge ${claim.status}`}>
          {claim.status.replace('_', ' ')}
        </div>
      </div>
      {hasDetails && (
        <div className="claim-details">
          {claim.note && <p className="claim-note"><strong>Auditor Note:</strong> {claim.note}</p>}
          {claim.original_text && <p className="claim-original-text"><strong>Original:</strong> {claim.original_text}</p>}
          {claim.sources && claim.sources.length > 0 && (
            <div className="claim-sources">
              <h5 className="claim-sources-title"><ExternalLink size={12} />Sources</h5>
              <div className="claim-source-list">
                {claim.sources.map((src, i) => (
                  <div key={i} className="claim-source-item">
                    <a href={src} target="_blank" rel="noopener noreferrer" className="claim-source-link">
                      {src}
                    </a>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CacheStats({ stats, onClear }) {
  return (
    <div className="cache-stats">
      <div className="cache-stat-item hits">
        <Database size={16} color="var(--green-bright)" />
        <div className="cache-stat-body">
          <div className="cache-stat-value">{stats.hits}</div>
          <div className="cache-stat-label">Cache Hits</div>
        </div>
      </div>
      <div className="cache-stat-item misses">
        <Database size={16} color="var(--orange)" />
        <div className="cache-stat-body">
          <div className="cache-stat-value">{stats.misses}</div>
          <div className="cache-stat-label">Cache Misses</div>
        </div>
      </div>
      <div className="cache-stat-item size">
        <Database size={16} color="var(--purple-bright)" />
        <div className="cache-stat-body">
          <div className="cache-stat-value">{stats.size} / {stats.maxsize}</div>
          <div className="cache-stat-label">Cache Size</div>
        </div>
      </div>
      <button onClick={onClear} className="cache-clear-btn" title="Clear Cache">
        <RefreshCw size={12} />
      </button>
    </div>
  )
}
