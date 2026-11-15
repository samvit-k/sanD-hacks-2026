// One chat message. Visual languages stay strictly separate:
//  - assistant prose: a ModelBox headed as a model interpretation (it may paraphrase, never quotes as fact);
//  - verified sources: VerifiedQuote blocks rebuilt by the server from stored PDF sentences;
//  - withheld answers: a warning card explaining why nothing is shown;
//  - status notes: small app-generated lines (uploads), never styled as model output.
import { ModelBox, VerifiedQuote } from '../inspector/parts.jsx'

const TOKEN_SPLIT = /(\[\[(?:q|paper:[a-z0-9][a-z0-9-]*|claim:\d+|rel:\d+)\]\])/g

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

export default function ChatMessage({ message, contextLabel, onReference, stale = false }) {
  const era = stale ? ' stale' : ''
  if (message.role === 'status') {
    return <div className={`chat-status ${message.tone ?? ''}`} role="status">{message.content}</div>
  }
  if (message.role === 'user') {
    return (
      <div className={`chat-row user${era}`}>
        <div className="chat-bubble">
          {message.context && contextLabel && <div className="chat-about">About: {contextLabel(message.context)}</div>}
          {message.content}
        </div>
      </div>
    )
  }
  if (message.guardrail) {
    return (
      <div className={`chat-row assistant${era}`} data-testid="assistant-message">
        <div className="warn-card" role="note"><b>Answer withheld.</b> {message.content}</div>
      </div>
    )
  }
  return (
    <div className={`chat-row assistant${era}`} data-testid="assistant-message">
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
        <div className="chat-nosources">No source sentences cited.</div>
      )}
    </div>
  )
}
