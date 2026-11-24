// One chat message. Visual languages stay strictly separate:
//  - assistant prose: a ModelBox headed as a model interpretation (it may paraphrase, never quotes as fact);
//  - verified sources: VerifiedQuote blocks rebuilt by the server from stored PDF sentences;
//  - withheld answers: a warning card explaining why nothing is shown;
//  - status notes: small app-generated lines (uploads), never styled as model output.
import { ModelBox, VerifiedQuote } from '../inspector/parts.jsx'

const TOKEN_SPLIT = /(\[\[(?:q|paper:[a-z0-9][a-z0-9-]*|claim:\d+|rel:\d+)\]\])/g

const lowerFirst = (t) => (t ? t[0].toLowerCase() + t.slice(1) : '')

export function referenceLabel(ref) {
  if (!ref) return null
  if (ref.kind === 'question') return 'Central Question'
  if (ref.kind === 'relationship') {
    const [a, b] = ref.refs ?? []
    return `Node ${a ?? '?'} ↔ Node ${b ?? '?'}`
  }
  return ref.ref != null ? `Node ${ref.ref}` : 'Node'
}

/** Assistant text with [[tokens]] rendered as clickable graph references. */
export function RichText({ text, references = {}, onReference }) {
  return (text ?? '').split(TOKEN_SPLIT).map((part, i) => {
    const m = /^\[\[(.+)\]\]$/.exec(part)
    if (!m) return <span key={i}>{part}</span>
    const ref = references[m[1]]
    if (!ref || ref.missing) {
      return <span key={i} className="ref-chip missing" title="This item is no longer in the graph">removed item</span>
    }
    return (
      <button key={i} type="button" className={`ref-chip ${ref.kind}`} onClick={() => onReference?.(ref)}
        title={`Show ${referenceLabel(ref)} in the graph`}>
        {referenceLabel(ref)}
      </button>
    )
  })
}

/** A turn whose evidence came from a paper that was later removed: kept for the record, no longer used. */
export function RemovedEvidenceNote({ message }) {
  if (!message.evidence_removed) return null
  const titles = (message.removed_evidence ?? []).map((p) => `“${p.title}”`)
  return (
    <div className="warn-card removed-evidence" role="note" data-testid="removed-evidence">
      <b>Evidence removed.</b> This answer relied on {titles.length ? titles.join(', ') : 'a paper'}, which was removed from
      the project. It is kept for the record, its quotes are no longer shown, and it is not used as context for new answers.
    </div>
  )
}

/** Minimize/expand control for one exchange (a question and the answer to it). */
function MinimizeButton({ collapsed, onToggle }) {
  return (
    <button type="button" className="chat-min-btn" onClick={onToggle} aria-expanded={!collapsed}
      title={collapsed ? 'Show this answer again' : 'Minimize this question and its answer'}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d={collapsed ? 'M4.5 6.5 8 10l3.5-3.5' : 'M4.5 9.5 8 6l3.5 3.5'} /></svg>
      {collapsed ? 'Expand' : 'Minimize'}
    </button>
  )
}

export default function ChatMessage({ message, contextLabel, onReference, stale = false, collapsed = false, onToggle = null }) {
  const era = stale ? ' stale' : ''
  if (message.role === 'status') {
    return <div className={`chat-status ${message.tone ?? ''}`} role="status">{message.content}</div>
  }
  if (message.role === 'user') {
    return (
      <div className={`chat-row user${era}${collapsed ? ' collapsed' : ''}${message.evidence_removed ? ' evidence-removed' : ''}`}
        data-testid="user-message">
        <div className="chat-bubble">
          {message.context && contextLabel && <div className="chat-about">About: {contextLabel(message.context)}</div>}
          <div className="chat-prompt" title={collapsed ? message.content : undefined}>{message.content}</div>
        </div>
        {onToggle && <MinimizeButton collapsed={collapsed} onToggle={onToggle} />}
        {collapsed && <div className="chat-minimized">Answer minimized</div>}
      </div>
    )
  }
  if (message.guardrail) {
    return (
      <div className={`chat-row assistant${era}`} data-testid="assistant-message">
        <RemovedEvidenceNote message={message} />
        <div className="warn-card" role="note"><b>Answer withheld.</b> {message.content}</div>
      </div>
    )
  }
  return (
    <div className={`chat-row assistant${era}${message.evidence_removed ? ' evidence-removed' : ''}`} data-testid="assistant-message">
      <RemovedEvidenceNote message={message} />
      {message.integrity && (
        // Shown BEFORE the answer: the server re-checked this stored answer against today's author information.
        <div className="indep indep-review chat-integrity" role="note" data-testid="chat-integrity">
          <div className="indep-head">⚠ {message.integrity.headline}</div>
          <p>This answer presents related findings as confirmation, but {lowerFirst(message.integrity.detail)}</p>
          <div className="insp-small">{message.integrity.note}</div>
        </div>
      )}
      <ModelBox title="Research assistant (Gemini) — interpretation, not a verdict">
        {message.insufficient_evidence && <div className="chat-flag">Insufficient evidence</div>}
        <p className="chat-text"><RichText text={message.content} references={message.references} onReference={onReference} /></p>
      </ModelBox>
      {message.cited?.length > 0 ? (
        <div className="chat-sources">
          <div className="chat-sources-head">Verified source sentences ({message.cited.length})</div>
          {message.cited.map((q) => <VerifiedQuote key={q.id} quote={q} paperTitle={q.paper_title} />)}
        </div>
      ) : (
        <div className="chat-nosources">
          {message.evidence_removed ? 'Its source sentences were removed with the paper.' : 'No source sentences cited.'}
        </div>
      )}
    </div>
  )
}