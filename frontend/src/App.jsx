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
  { key: 'proposer',  icon: Cpu,       label: 'Proposer'  },
  { key: 'extractor', icon: BarChart3,  label: 'Extractor' },
  { key: 'auditor',   icon: ShieldCheck, label: 'Auditor'  },
  { key: 'resolver',  icon: GitMerge,   label: 'Resolver'  },
]

const STATUS_STEP_MAP = {
  proposer: 0, claim: 1, extract: 1,
  auditor: 2, audit: 2, resolver: 3, resolv: 3,
}

const HISTORY_KEY = 'consensusflow:history'
const MAX_HISTORY = 50

// ── Utilities ─────────────────────────────────────────────────────────────────

function scoreColor(s) {
  if (s >= 80) return 'var(--green)'
  if (s >= 55) return 'var(--yellow)'
  return 'var(--red)'
}
function scoreHalo(s) {
  if (s >= 80) return 'rgba(52,211,153,.22)'
  if (s >= 55) return 'rgba(251,191,36,.18)'
  return 'rgba(248,113,113,.2)'
}
function scoreLine(s) {
  if (s >= 80) return 'rgba(52,211,153,.5)'
  if (s >= 55) return 'rgba(251,191,36,.45)'
  return 'rgba(248,113,113,.5)'
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
  if (diff < 60)    return 'just now'
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
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
      run_id:           report.run_id,
      prompt:           report.prompt,
      status:           report.status,
      gotcha_score:     report.gotcha_score?.score ?? null,
      total_tokens:     report.total_tokens,
      total_latency_ms: report.total_latency_ms,
      created_at:       report.created_at || new Date().toISOString(),
      chain_models:     report.chain_models || [],
      _full:            report,
    }
    const next = [entry, ...history.filter(h => h.run_id !== report.run_id)]
    localStorage.setItem(HISTORY_KEY, JSON.stringify(next.slice(0, MAX_HISTORY)))
  } catch (e) { console.warn('History save failed:', e) }
}
function clearHistory() { localStorage.removeItem(HISTORY_KEY) }

// ── History Panel ──────────────────────────────────────────────────────────────

function HistoryPanel({ onRestore }) {
  const [history, setHistory] = useState(loadHistory)
  const [search, setSearch]   = useState('')

  const handleClear = () => { clearHistory(); setHistory([]) }

  const filtered = search.trim()
    ? history.filter(h => h.prompt.toLowerCase().includes(search.toLowerCase()))
    : history

  if (history.length === 0) return null

  return (
    <div className="history-panel">
      <div className="history-panel-header">
        <div style={{ display:'flex', alignItems:'center', gap:12, flex:1 }}>
          <div className="history-panel-title">
            <History size={13} />
            History
            <span className="history-count">{history.length}</span>
          </div>
          <div style={{ flex:1, maxWidth:240, position:'relative' }}>
            <Search size={11} style={{ position:'absolute', left:9, top:'50%', transform:'translateY(-50%)', color:'var(--muted)', pointerEvents:'none' }} />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search history…"
              style={{ width:'100%', padding:'5px 10px 5px 26px', background:'rgba(255,255,255,.04)', border:'1px solid var(--border)', borderRadius:8, color:'var(--text2)', fontSize:'.75rem', outline:'none' }}
            />
          </div>
        </div>
        <button className="history-clear-btn" onClick={handleClear}>
          <Trash2 size={10} style={{ marginRight:3 }} />Clear all
        </button>
      </div>
      <div className="history-list">
        {filtered.length === 0 && <div className="history-empty">No results matching &quot;{search}&quot;</div>}
        {filtered.map(item => {
          const score = item.gotcha_score
          const color = score != null ? scoreColor(score) : 'var(--muted)'
          return (
            <div key={item.run_id} className="history-item" onClick={() => onRestore(item._full)} title="Click to restore this verification">
              <div className="history-item-score" style={{ color, borderColor:color, background:`${color}15` }}>
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
              <div className="history-item-status" style={{ background:`${color}18`, color, border:`1px solid ${color}40` }}>
                {item.status}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── SVG Score Ring ─────────────────────────────────────────────────────────────

function ScoreRing({ score, color }) {
  const R = 56
  const C = 2 * Math.PI * R
  const [dash, setDash] = useState(C)
  useEffect(() => {
    const t = setTimeout(() => setDash(C - (score / 100) * C), 100)
    return () => clearTimeout(t)
  }, [score, C])
  return (
    <div className="score-ring-wrap">
      <svg className="score-ring-svg" viewBox="0 0 140 140">
        <circle className="score-ring-bg"    cx="70" cy="70" r={R} />
        <circle className="score-ring-trail" cx="70" cy="70" r={R} style={{ stroke:color, strokeDasharray:C, strokeDashoffset:0 }} />
        <circle className="score-ring-fill"  cx="70" cy="70" r={R} style={{ stroke:color, strokeDasharray:C, strokeDashoffset:dash }} />
      </svg>
      <div className="score-ring-center">
        <span className="score-ring-num" style={{ color }}>{score}</span>
        <span className="score-ring-denom">/100</span>
      </div>
    </div>
  )
}

// ── Score Card ─────────────────────────────────────────────────────────────────

function ScoreCard({ gs }) {
  const color = scoreColor(gs.score)
  return (
    <div className="score-card" style={{ '--score-halo': scoreHalo(gs.score), '--score-line': scoreLine(gs.score) }}>
      <ScoreRing score={gs.score} color={color} />
      <div className="score-grade-badge" style={{ color }}>{gs.emoji} {gs.grade}</div>
      <div className="score-label">{gs.label}</div>
      {gs.share_text && <div className="score-share">&quot;{gs.share_text}&quot;</div>}
    </div>
  )
}

// ── Stats Card ─────────────────────────────────────────────────────────────────

function StatsCard({ gs, savings, report }) {
  const pct = savings?.percent_saved ?? 0
  return (
    <div className="stats-card">
      <div className="stats-card-title">Pipeline Stats</div>
      <div className="stat-grid">
        <div className="stat-box">
          <div className="stat-box-val" style={{ color:'var(--green)' }}>{gs.total_claims - gs.catches}</div>
          <div className="stat-box-lbl">✓ Verified</div>
        </div>
        <div className="stat-box">
          <div className="stat-box-val" style={{ color:'var(--blue)' }}>{gs.catches}</div>
          <div className="stat-box-lbl">⚡ Caught</div>
        </div>
        <div className="stat-box">
          <div className="stat-box-val">{report.total_tokens.toLocaleString()}</div>
          <div className="stat-box-lbl"><Hash size={10} style={{ display:'inline', marginRight:2 }} />Tokens</div>
        </div>
        <div className="stat-box">
          <div className="stat-box-val">{(report.total_latency_ms / 1000).toFixed(1)}s</div>
          <div className="stat-box-lbl"><Timer size={10} style={{ display:'inline', marginRight:2 }} />Latency</div>
        </div>
      </div>
      {savings && (
        <>
          <div className="savings-bar-wrap">
            <div className="savings-bar-label">
              <span style={{ display:'flex', alignItems:'center', gap:5 }}><TrendingUp size={11} style={{ color:'var(--green)' }} />Token savings</span>
              <span style={{ color:'var(--green)', fontWeight:700 }}>{pct.toFixed(0)}%</span>
            </div>
            <div className="savings-bar-track"><div className="savings-bar-fill" style={{ width:`${pct}%` }} /></div>
          </div>
          <div className="pills-row">
            {savings.early_exit && <div className="early-exit-pill"><Zap size={10} /> Early Exit</div>}
            <span className="cost-text">Est. cost: <strong style={{ color:'var(--text)' }}>${savings.cost_usd.toFixed(5)}</strong></span>
            {savings.saved_usd > 0 && <span className="saved-text">Saved: ${savings.saved_usd.toFixed(5)}</span>}
          </div>
        </>
      )}
      {gs.failure_taxonomy && Object.keys(gs.failure_taxonomy).length > 0 && (
        <div className="taxonomy-row">
          {Object.entries(gs.failure_taxonomy).map(([k, v]) => (
            <div className="taxonomy-pill" key={k}><span>{v}</span>{k}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Copy Button ────────────────────────────────────────────────────────────────

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }
  return (
    <button className="copy-btn" onClick={copy}>
      {copied ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy</>}
    </button>
  )
}

// ── Answer Card ────────────────────────────────────────────────────────────────

function AnswerCard({ report }) {
  return (
    <div className="answer-card">
      <div className="answer-card-header">
        <span className="answer-card-header-left">
          <CheckCircle2 size={12} style={{ color:'var(--green)' }} />
          Verified Answer
        </span>
        <div className="answer-card-header-right">
          <span className={clsx('answer-status-pill', `status-${report.status}`)}>{report.status}</span>
          <CopyButton text={report.final_answer} />
        </div>
      </div>
      <div className="answer-body">{report.final_answer}</div>
    </div>
  )
}

// ── Model Steps ────────────────────────────────────────────────────────────────

function StepsSection({ steps }) {
  const entries = Object.entries(steps).filter(([, s]) => s !== null)
  if (!entries.length) return null
  const classMap = { proposer:'step-proposer', auditor:'step-auditor', resolver:'step-resolver', extractor:'step-extractor' }
  return (
    <div className="steps-section">
      <div className="section-title">Model Performance</div>
      <div className="steps-grid">
        {entries.map(([name, s]) => (
          <div className="step-card" key={name}>
            <div className="step-card-top">
              <span className={clsx('step-name-badge', classMap[name] ?? '')}>{name}</span>
              <span className="step-latency">{s.latency_ms.toFixed(0)} ms</span>
            </div>
            <div className="step-model" title={s.model}>{s.model}</div>
            <div className="step-tokens">
              {s.prompt_tokens.toLocaleString()} + {s.completion_tokens.toLocaleString()} = <strong>{s.total_tokens.toLocaleString()}</strong> tok
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Claims Section ─────────────────────────────────────────────────────────────

const FILTERS = ['ALL', 'VERIFIED', 'CORRECTED', 'DISPUTED', 'REJECTED', 'NUANCED']

function ClaimsSection({ claims }) {
  const [filter, setFilter] = useState('ALL')
  if (!claims?.length) return null

  const counts = claims.reduce((acc, c) => {
    acc[c.status] = (acc[c.status] ?? 0) + 1
    return acc
  }, {})

  const visible = filter === 'ALL' ? claims : claims.filter(c => c.status === filter)

  const iconMap = {
    VERIFIED:  <CheckCircle2 size={10} />,
    CORRECTED: <RefreshCw size={10} />,
    DISPUTED:  <AlertTriangle size={10} />,
    REJECTED:  <XCircle size={10} />,
    NUANCED:   <AlertTriangle size={10} />,
  }

  return (
    <div className="claims-section">
      <div className="claims-header">
        <div className="section-title" style={{ marginBottom:0 }}>
          Atomic Claims — {claims.length} total
        </div>
        <div className="claims-filter">
          {FILTERS.filter(f => f === 'ALL' || counts[f]).map(f => (
            <button key={f} className={clsx('filter-chip', filter === f && 'active')} onClick={() => setFilter(f)}>
              {f === 'ALL' ? `All (${claims.length})` : `${f.charAt(0)}${f.slice(1).toLowerCase()} (${counts[f]})`}
            </button>
          ))}
        </div>
      </div>
      <div className="claims-list">
        {visible.map((c, i) => (
          <div className="claim-row" key={c.id ?? i} style={{ animationDelay:`${i * 28}ms` }}>
            <div className="claim-left">
              <span className={`claim-badge badge-${c.status}`}>{iconMap[c.status]} {c.status}</span>
              <div className="claim-conf">{(c.confidence * 100).toFixed(0)}%</div>
            </div>
            <div className="claim-body">
              <div className="claim-text">{c.text}</div>
              {c.original_text && <div className="claim-orig">Was: {c.original_text}</div>}
              {c.note && <div className="claim-note">{c.note}</div>}
              {c.sources?.length > 0 && (
                <div className="claim-sources">
                  {c.sources.map((url, si) => {
                    let host = url
                    try { host = new URL(url).hostname.replace('www.', '') } catch {}
                    return (
                      <a key={si} href={url} target="_blank" rel="noopener noreferrer" className="source-link" title={url}>
                        <ExternalLink size={9} />{host}
                      </a>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Pipeline Header ────────────────────────────────────────────────────────────

function PipelineHeader({ activeStep }) {
  return (
    <div className="header-pipeline">
      {PIPELINE_STEPS.map((step, idx) => {
        const isActive = activeStep === idx
        const isDone   = activeStep > idx
        return (
          <div key={step.key} style={{ display:'flex', alignItems:'center' }}>
            <div className={clsx('pipe-node', isActive && 'active', isDone && 'done')}>
              <span className="pipe-dot" />
              <step.icon size={11} />
              {step.label}
            </div>
            {idx < PIPELINE_STEPS.length - 1 && (
              <div className={clsx('pipe-connector', (isActive || isDone) && (isDone ? 'done' : 'active'))}>
                <div className="pipe-connector-track" />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Stream Panel ───────────────────────────────────────────────────────────────

const TAB_LABELS = { all:'All', proposer:'Proposer', auditor:'Auditor', resolver:'Resolver' }

function StreamPanel({ buffers, activeTab, setActiveTab, isLive }) {
  const ref = useRef(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [buffers, activeTab])

  const display = activeTab === 'all'
    ? Object.values(buffers).flat()
    : (buffers[activeTab] ?? [])

  const hasBuf = k => (buffers[k]?.length ?? 0) > 0

  return (
    <div className="stream-panel">
      <div className="stream-panel-header">
        <div className="live-indicator">
          {isLive && <span className="live-dot" />}
          {isLive ? 'Live Stream' : 'Stream Log'}
        </div>
        <div className="stream-tabs">
          {Object.entries(TAB_LABELS).map(([k, label]) => (
            <button key={k} className={clsx('stream-tab', activeTab === k && 'active', hasBuf(k) && 'has-content')} onClick={() => setActiveTab(k)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="stream-box" ref={ref}>
        {display.map((l, i) => <span key={i} className={l.cls}>{l.text}</span>)}
        {isLive && <span className="stream-cursor" />}
      </div>
    </div>
  )
}

// ── Full Report ────────────────────────────────────────────────────────────────

function ReportView({ report }) {
  return (
    <>
      <div className="report-grid">
        <ScoreCard gs={report.gotcha_score} />
        <StatsCard gs={report.gotcha_score} savings={report.savings} report={report} />
      </div>
      <AnswerCard report={report} />
      <StepsSection steps={report.steps} />
      <ClaimsSection claims={report.atomic_claims} />
    </>
  )
}

// ── Main App ───────────────────────────────────────────────────────────────────

export default function App() {
  const [prompt,    setPrompt]    = useState('')
  const [streaming, setStreaming] = useState(true)
  const [loading,   setLoading]   = useState(false)
  const [status,    setStatus]    = useState('')
  const [error,     setError]     = useState('')
  const [report,    setReport]    = useState(null)
  const [activeStep,  setActiveStep]  = useState(-1)
  const [streamTab,   setStreamTab]   = useState('all')
  const [buffers, setBuffers] = useState({ all:[], proposer:[], auditor:[], resolver:[] })
  const [historyTick, setHistoryTick] = useState(0)

  const abortRef = useRef(null)

  const addLine = useCallback((text, cls = '', bucket = 'all') => {
    const line = { text, cls }
    setBuffers(prev => {
      const next = { ...prev, all: [...prev.all, line] }
      if (bucket !== 'all') next[bucket] = [...(prev[bucket] ?? []), line]
      return next
    })
  }, [])

  const reset = () => {
    setError(''); setReport(null); setStatus(''); setActiveStep(-1)
    setStreamTab('all')
    setBuffers({ all:[], proposer:[], auditor:[], resolver:[] })
  }

  const cancel = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false); setStatus('Cancelled.'); setActiveStep(-1)
  }

  const onReportComplete = useCallback((data) => {
    setReport(data)
    setStatus('Complete')
    setActiveStep(4)
    saveToHistory(data)
    setHistoryTick(t => t + 1)
  }, [])

  // ── SSE streaming ──────────────────────────────────────────────────────────
  const runStream = async () => {
    reset(); setLoading(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const res = await fetch('/api/verify/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
        signal: ctrl.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail ?? res.statusText)
      }
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream:true })
        const parts = buf.split('\n\n')
        buf = parts.pop()
        for (const part of parts) {
          const line = part.replace(/^data:\s*/, '').trim()
          if (!line) continue
          let msg
          try { msg = JSON.parse(line) } catch { continue }
          const { event, data } = msg
          switch (event) {
            case 'status':
              setStatus(data); setActiveStep(inferStep(data))
              addLine(`\n${data}\n`, 'chunk-status')
              break
            case 'proposer_chunk':
              setActiveStep(0); addLine(data, 'chunk-proposer', 'proposer')
              break
            case 'claims_extracted':
              setActiveStep(1)
              addLine(`\n\n📋  ${data.length} atomic claims extracted\n`, 'chunk-event')
              break
            case 'auditor_chunk':
              setActiveStep(2); addLine(data, 'chunk-auditor', 'auditor')
              break
            case 'early_exit':
              setActiveStep(3)
              addLine(`\n⚡  ${data.message}  (saved ~${data.saved_tokens?.toLocaleString() ?? '?'} tokens)\n`, 'chunk-event')
              break
            case 'resolver_chunk':
              setActiveStep(3); addLine(data, 'chunk-resolver', 'resolver')
              break
            case 'error':
              setError(data); addLine(`\n⚠  ${data}\n`, 'chunk-error')
              break
            case 'done':
              onReportComplete(data)
              break
          }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message)
    } finally {
      setLoading(false); abortRef.current = null
    }
  }

  // ── Blocking verify ────────────────────────────────────────────────────────
  const runBlocking = async () => {
    reset(); setLoading(true); setStatus('Verifying…'); setActiveStep(0)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const res = await fetch('/api/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
        signal: ctrl.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail ?? res.statusText)
      }
      const data = await res.json()
      onReportComplete(data)
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message)
    } finally {
      setLoading(false); abortRef.current = null
    }
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!prompt.trim()) return
    streaming ? runStream() : runBlocking()
  }

  const handleRestore = (fullReport) => {
    setReport(fullReport)
    setPrompt(fullReport.prompt || '')
    setStatus('Restored from history')
    setActiveStep(4)
    setError('')
    setBuffers({ all:[], proposer:[], auditor:[], resolver:[] })
  }

  const showStream   = streaming && Object.values(buffers).some(b => b.length > 0)
  const pipelineStep = loading ? activeStep : (report ? 4 : -1)

  return (
    <>
      {/* ── Animated aurora background ─── */}
      <div className="aurora" aria-hidden>
        <div className="aurora-blob aurora-blob-1" />
        <div className="aurora-blob aurora-blob-2" />
        <div className="aurora-blob aurora-blob-3" />
      </div>
      <div className="bg-grid" aria-hidden />

      <div className="app">

        {/* ── Header ─── */}
        <header className="header">
          <div className="header-badge">
            <span className="header-badge-dot" />
            <ShieldCheck size={11} />
            Trust Protocol
          </div>
          <h1>
            <span className="word-consensus">Consensus</span>
            <span className="word-flow">Flow</span>
          </h1>
          <p className="header-subtitle">
            Multi-model fact-checking pipeline.<br />
            Every claim decomposed, audited, and resolved.
          </p>
          <PipelineHeader activeStep={pipelineStep} />
        </header>

        {/* ── History Panel ─── */}
        {!loading && <HistoryPanel key={historyTick} onRestore={handleRestore} />}

        {/* ── Examples ─── */}
        {!loading && !report && (
          <div className="examples">
            {EXAMPLES.map(ex => (
              <button key={ex} className="example-chip" onClick={() => setPrompt(ex)} title={ex}>
                <Sparkles size={9} style={{ marginRight:4, opacity:.6 }} />
                {ex}
              </button>
            ))}
          </div>
        )}

        {/* ── Form ─── */}
        <form className="query-form" onSubmit={handleSubmit}>
          <div className="textarea-wrap">
            <textarea
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              placeholder={'Ask anything that needs fact-checking…\ne.g. What time does Topkapi Palace open? Is the Blue Mosque free?'}
              disabled={loading}
              onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit(e) }}
              maxLength={8000}
            />
            {prompt.length > 200 && (
              <div className="char-count">{prompt.length.toLocaleString()} / 8,000</div>
            )}
          </div>
          <div className="form-bottom">
            <button className="btn-verify" type="submit" disabled={loading || !prompt.trim()}>
              {loading
                ? <><RefreshCw size={14} style={{ animation:'spin .75s linear infinite' }} />&nbsp; Verifying…</>
                : <><ChevronRight size={14} />&nbsp; Verify</>}
            </button>
            {loading && <button className="btn-ghost" type="button" onClick={cancel}>✕&nbsp; Cancel</button>}
            {report && !loading && (
              <button className="btn-ghost" type="button" onClick={() => { reset(); setPrompt('') }}>
                ↩&nbsp; New query
              </button>
            )}
            <label className="toggle-stream" onClick={() => !loading && setStreaming(s => !s)}>
              <div className={clsx('toggle-track', streaming && 'on')}><div className="toggle-thumb" /></div>
              <span className="toggle-label">Stream live</span>
            </label>
          </div>
        </form>

        {/* ── Status bar ─── */}
        {(loading || (status && status !== 'Complete')) && (
          <div className={clsx('status-bar', loading && 'running', error && 'error')}>
            {loading && <div className="spinner" />}
            <span>{status || 'Starting…'}</span>
          </div>
        )}

        {/* ── Error ─── */}
        {error && (
          <div className="error-box">
            <AlertTriangle size={15} style={{ flexShrink:0, marginTop:1 }} />
            <span>{error}</span>
          </div>
        )}

        {/* ── Live stream panel ─── */}
        {showStream && (
          <StreamPanel buffers={buffers} activeTab={streamTab} setActiveTab={setStreamTab} isLive={loading} />
        )}

        {/* ── Report ─── */}
        {report && <ReportView report={report} />}

      </div>
    </>
  )
}
