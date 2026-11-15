// Right-hand research assistant: PDF uploads (existing ingestion pipeline) + evidence-grounded chat.
// Styled like the left workspace sidebar. Kept mounted while closed so drafts and state survive.
import { useEffect, useRef, useState } from 'react'
import { describeExtraction } from '../../graph/extraction.js'
import { MESSAGES, STATUS } from '../../state/useUploads.js'
import ChatMessage from './ChatMessage.jsx'

const MODES = {
  library: {
    label: 'Add to library',
    help: 'Foundational papers. Claims are extracted and compared with the rest of the library. Several PDFs at once is fine.',
    stages: 'extracting claims, comparing with the library',
  },
  evaluate: {
    label: 'Evaluate a new paper',
    help: 'Checks how ONE new paper strengthens, challenges or qualifies the existing library (impact analysis).',
    stages: 'extracting claims, comparing, assessing impact',
  },
}
const SUGGESTIONS = [
  'Where do the papers in the library disagree?',
  'Which findings support each other?',
  'What changed after the latest evaluated paper?',
]

function useTick(active) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

const mmss = (ms) => {
  const s = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function resultSummary(item) {
  const r = item.result
  if (!r) return null
  if (item.fromServer) return `Confirmed on the server: “${r.paper?.title ?? 'paper'}”.`
  const outcome = describeExtraction(r.extraction)
  if (outcome) return outcome.title
  if (r.mode === 'evaluate') {
    const c = r.counts ?? {}
    const parts = [['challenges', 'challenges'], ['qualifies', 'qualifies'], ['strengthens', 'strengthens'],
      ['leaves_unresolved', 'leaves unresolved'], ['not_addressed', 'not addressed']]
      .filter(([k]) => c[k]).map(([k, label]) => `${c[k]} ${label}`)
    return `${r.new_claims?.length ?? 0} claims · impact: ${parts.join(' · ') || 'none recorded'}`
  }
  return `${r.claims_kept ?? 0} claims · ${r.relationships_created ?? 0} new relationships`
}

function UploadRow({ item, now, uploads }) {
  const label = {
    [STATUS.queued]: 'Queued',
    [STATUS.analyzing]: `Analyzing · ${mmss(now - (item.startedAt ?? now))}`,
    [STATUS.checking]: 'Checking the server…',
    [STATUS.done]: describeExtraction(item.result?.extraction) ? 'Analyzed' : 'Done',
    [STATUS.failed]: item.duplicate ? 'Already in the graph' : 'Failed',
    [STATUS.unknown]: 'Result not confirmed',
  }[item.status]
  return (
    <li className={`up-row up-${item.status}`}>
      <div className="up-top">
        <span className="up-name" title={item.name}>{item.name}</span>
        <span className="up-mode">{item.mode === 'evaluate' ? 'evaluate' : 'library'}</span>
      </div>
      <div className="up-status">
        {(item.status === STATUS.analyzing || item.status === STATUS.checking) && <span className="spinner" aria-hidden="true" />}
        <b>{label}</b>
        {item.status === STATUS.analyzing && <span> — {MODES[item.mode].stages}; usually 20–80 s</span>}
      </div>
      {item.status === STATUS.done && (
        <div className={`up-detail ${describeExtraction(item.result?.extraction) ? 'warn' : ''}`}>{resultSummary(item)}</div>
      )}
      {item.message && item.message !== item.result?.warning && <div className="up-detail">{item.message}</div>}
      {item.refreshFailed && (
        <div className="up-detail warn">{MESSAGES.refreshFailed}{' '}
          <button type="button" className="link-btn" onClick={() => uploads.retryRefresh(item.key)}>Refresh graph</button>
        </div>
      )}
      <div className="up-actions">
        {item.status === STATUS.unknown && (
          <button type="button" className="link-btn" onClick={() => uploads.checkAgain(item.key)}>Check again</button>
        )}
        {uploads.canRetry(item) && !item.duplicate && (
          <button type="button" className="link-btn" onClick={() => uploads.retry(item.key)}>Retry upload</button>
        )}
        {(item.status === STATUS.done || item.status === STATUS.failed) && (
          <button type="button" className="link-btn muted" onClick={() => uploads.dismiss(item.key)}>Dismiss</button>
        )}
      </div>
    </li>
  )
}

function UploadSection({ uploads, canUpload, blockedReason, hasLibrary }) {
  const [mode, setMode] = useState('library')
  const [rejected, setRejected] = useState([])
  const [drag, setDrag] = useState(false)
  const inputRef = useRef(null)
  const now = useTick(uploads.items.some((x) => x.status === STATUS.analyzing))
  const evalDisabled = !hasLibrary

  const take = (files) => {
    if (!canUpload || !files?.length) return
    const { rejected: r } = uploads.add(files, mode)
    setRejected(r)
  }

  return (
    <section className="as-section" aria-label="Upload PDFs">
      <div className="sb-section">Add papers</div>
      <div className="seg" role="radiogroup" aria-label="Upload purpose">
        {Object.entries(MODES).map(([key, m]) => (
          <button key={key} type="button" role="radio" aria-checked={mode === key}
            className={`seg-btn ${mode === key ? 'on' : ''}`} disabled={key === 'evaluate' && evalDisabled}
            title={key === 'evaluate' && evalDisabled ? 'Add library papers first: there is nothing to evaluate against yet.' : m.help}
            onClick={() => setMode(key)}>
            {m.label}
          </button>
        ))}
      </div>
      <p className="as-help">{MODES[mode].help}</p>
      <div
        className={`drop ${drag ? 'drag' : ''} ${canUpload ? '' : 'disabled'}`}
        onDragOver={(e) => { e.preventDefault(); if (canUpload) setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); take(e.dataTransfer.files) }}
      >
        <button type="button" className="btn btn-sm" disabled={!canUpload} onClick={() => inputRef.current?.click()}>
          {mode === 'evaluate' ? 'Choose a PDF to evaluate…' : 'Choose PDFs…'}
        </button>
        <span className="drop-hint">{canUpload ? 'or drop PDF files here' : blockedReason}</span>
        <input ref={inputRef} type="file" accept="application/pdf,.pdf" multiple={mode === 'library'} hidden
          data-testid="pdf-input"
          onChange={(e) => { take(e.target.files); e.target.value = '' }} />
      </div>
      {rejected.length > 0 && (
        <ul className="up-rejected" role="alert">
          {rejected.map((r) => <li key={r.name}><b>{r.name}</b>: {r.reason}</li>)}
        </ul>
      )}
      {uploads.items.length > 0 && (
        <ul className="up-list">{uploads.items.map((it) => <UploadRow key={it.key} item={it} now={now} uploads={uploads} />)}</ul>
      )}
    </section>
  )
}

/** Messages written under an earlier central question are marked, with a divider where the question changed.
 * The server also keeps those turns out of the model's context. */
export function withQuestionDividers(messages, currentQuestion) {
  const out = []
  let last = null
  for (const m of messages) {
    const q = m.role === 'status' ? null : m.question ?? null
    if (q && q !== last) {
      if (q !== currentQuestion) out.push({ divider: true, key: `div-${m.id}`, text: 'Asked under an earlier central question', question: q })
      else if (last) out.push({ divider: true, key: `div-${m.id}`, text: 'Current central question', question: q })
      last = q
    }
    out.push({ message: m, stale: !!q && !!currentQuestion && q !== currentQuestion })
  }
  return out
}

function ChatSection({ chat, chatEnabled, chatBlockedReason, context, contextLabel, onClearContext, onReference, focusTick,
  currentQuestion, evidenceNote }) {
  const [draft, setDraft] = useState('')
  const listRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chat.messages.length, chat.pending, chat.error])
  useEffect(() => {
    if (focusTick) inputRef.current?.focus()
  }, [focusTick])

  const submit = async () => {
    const text = draft.trim()
    if (!text || chat.pending) return
    setDraft('')
    const ok = await chat.send(text, context)
    if (ok) onClearContext?.()
  }

  return (
    <section className="as-chat" aria-label="Research assistant chat">
      <div className="sb-section">Ask your research</div>
      <div className="chat-list" ref={listRef} aria-live="polite">
        {chat.messages.length === 0 && !chat.pending && (
          <div className="chat-empty">
            <p>Ask about your papers, claims and evidence relationships. Answers cite verified sentences from your PDFs;
              unsupported answers are withheld.</p>
            {evidenceNote && <p className="chat-evidence-note" role="note">{evidenceNote}</p>}
            {chatEnabled && SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="chip-btn" onClick={() => { setDraft(s); inputRef.current?.focus() }}>{s}</button>
            ))}
          </div>
        )}
        {withQuestionDividers(chat.messages, currentQuestion).map((row) => (row.divider ? (
          <div key={row.key} className="chat-divider" role="separator"><span>{row.text}:</span> {row.question}</div>
        ) : (
          <ChatMessage key={row.message.id} message={row.message} stale={row.stale} contextLabel={contextLabel}
            onReference={onReference} />
        )))}
        {chat.pending && (
          <>
            <div className="chat-row user pending">
              <div className="chat-bubble">
                {chat.pending.context && <div className="chat-about">About: {contextLabel(chat.pending.context)}</div>}
                {chat.pending.text}
                <div className="chat-sending">sending…</div>
              </div>
            </div>
            <div className="chat-thinking" role="status"><span className="spinner" aria-hidden="true" /> Reviewing the evidence…</div>
          </>
        )}
        {chat.error && (
          <div className="warn-card" role="alert">
            {chat.error.kind === 'send' ? 'Your message was not answered or saved: ' : 'Could not load the conversation: '}
            {chat.error.message}{' '}
            <button type="button" className="link-btn" onClick={() => chat.retry()}>Retry</button>
          </div>
        )}
      </div>
      <div className="chat-compose">
        {context && (
          <div className="ctx-chip">
            <span>About: <b>{contextLabel(context)}</b></span>
            <button type="button" className="icon-btn" aria-label="Remove attached item" onClick={onClearContext}>
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
            </button>
          </div>
        )}
        <div className="compose-row">
          <textarea ref={inputRef} rows={2} value={draft} maxLength={2000} disabled={!chatEnabled}
            placeholder={chatEnabled ? 'Ask, e.g. “Why do Nodes 3 and 6 agree?”' : chatBlockedReason}
            aria-label="Message the research assistant"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }} />
          <button type="button" className="btn send-btn" disabled={!chatEnabled || !draft.trim() || !!chat.pending}
            onClick={submit} aria-label="Send">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8h10M8.5 4l4 4-4 4" /></svg>
          </button>
        </div>
      </div>
    </section>
  )
}

export default function AssistantPanel({
  open, onClose, chat, uploads, canUpload, uploadBlockedReason, hasLibrary, chatEnabled, chatBlockedReason,
  context, contextLabel, onClearContext, onReference, focusTick, currentQuestion = null, evidenceNote = '',
}) {
  return (
    <aside id="assistant-panel" className="assistant" hidden={!open} aria-label="Research assistant">
      <div className="sb-head as-head">
        <span className="sb-brand">Research assistant</span>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close research assistant">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" /></svg>
        </button>
      </div>
      <div className="as-body">
        <UploadSection uploads={uploads} canUpload={canUpload} blockedReason={uploadBlockedReason} hasLibrary={hasLibrary} />
        <ChatSection chat={chat} chatEnabled={chatEnabled} chatBlockedReason={chatBlockedReason} context={context}
          contextLabel={contextLabel} onClearContext={onClearContext} onReference={onReference} focusTick={focusTick}
          currentQuestion={currentQuestion} evidenceNote={evidenceNote} />
      </div>
    </aside>
  )
}
