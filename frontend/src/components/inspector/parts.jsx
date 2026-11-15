// Shared inspector building blocks. Two visual languages are kept strictly apart:
//  - VerifiedQuote: verbatim sentences rebuilt by the server from the stored PDF text.
//  - ModelBox: anything Gemini produced (claim paraphrases, explanations, rationales).
import { CONDITION_LABELS, DIFF_FIELDS, RELATIONSHIPS, SECTION_LABELS } from '../../graph/vocab.js'

const NOT_REPORTED = 'not reported'

const KIND_LABELS = { caption: 'Caption', table: 'Table / figure text', footnote: 'Footnote' }
const ARTIFACT_LABELS = {
  caption_inside: 'caption text', footnote_glued: 'footnote text', word_fragment: 'a split word',
  table_inside_prose: 'table values', cut_at_page_end: 'a sentence cut at the page end',
}

/** How far the quotation's wording can be trusted as-is (the citation itself is always verified). */
export function quoteProvenance(quote) {
  const artifacts = (quote.artifacts ?? []).map((a) => ARTIFACT_LABELS[a] ?? a)
  if (quote.text_check === 'joined_blocks') {
    return { tone: 'check', text: `Joined across PDF layout blocks — verify the wording on p. ${quote.page}` }
  }
  if (artifacts.length) {
    return { tone: 'check', text: `May contain PDF layout artifacts (${artifacts.join(', ')}) — verify on p. ${quote.page}` }
  }
  if (quote.text_check === 'single_block') {
    return { tone: 'ok', text: 'Source text from one PDF text block (normalized ligatures, hyphens and spacing)' }
  }
  return null
}

export function VerifiedQuote({ quote, paperTitle }) {
  const prov = quoteProvenance(quote)
  return (
    <figure className={`vq${prov?.tone === 'check' ? ' vq-check' : ''}`}>
      <blockquote>{quote.text}</blockquote>
      <figcaption>
        <span className="vq-ok" aria-hidden="true">✓</span> Verified source sentence · p. {quote.page}
        {quote.section && quote.section !== 'front' ? ` · ${SECTION_LABELS[quote.section] ?? quote.section}` : ''}
        {KIND_LABELS[quote.kind] ? ` · ${KIND_LABELS[quote.kind]}` : ''}
        {' · '}<code title="Sentence ID (citation)">{quote.id}</code>
        {prov && <div className={`vq-prov ${prov.tone}`} data-testid="quote-provenance">{prov.text}</div>}
        {paperTitle ? <div className="vq-paper">{paperTitle}</div> : null}
      </figcaption>
    </figure>
  )
}

/** Dependence between the two papers behind a relationship or impact. `independence` comes from the server:
 *  {status, shared, possible, summary, level: 'review' | 'note' | null, headline, basis_sentence_ids}. */
export function IndependenceNotice({ independence }) {
  if (!independence) return null
  const { level, headline, summary, basis_sentence_ids: basis } = independence
  const shared = independence.status === 'shared_authors' || independence.status === 'possible_shared_authors'
  if (!level && !shared) return null
  return (
    <div className={`indep indep-${level ?? 'context'}`} role="note" data-testid="independence">
      {headline && <div className="indep-head">{level === 'review' ? '⚠ ' : ''}{headline}</div>}
      <p>{summary}</p>
      {basis?.length > 0 && (
        <div className="insp-small">
          Author lists parsed from the papers’ first-page text ({basis.map((b, i) => <code key={b}>{i ? ', ' : ''}{b}</code>)}).
          Shared authorship links studies but does not prove shared data; no detected overlap would not prove independence.
        </div>
      )}
    </div>
  )
}

const REVIEWER = {
  automated: 'Automated check',
  model: 'AI model recommendation — not a human decision',
  human: 'Human review',
}
const REVIEW_STATUS = { proposed: 'Proposed — not applied', accepted: 'Accepted', rejected: 'Rejected' }

/** Classification audit trail. Original labels stay unchanged; proposals are never applied automatically. */
export function ReviewList({ reviews, labelOf }) {
  if (!reviews?.length) return null
  return (
    <ul className="review-list" data-testid="reviews">
      {reviews.map((r) => (
        <li key={r.id} className={`review review-${r.status}`}>
          <div className="review-head">
            <span className={`review-status ${r.status}`}>{REVIEW_STATUS[r.status] ?? r.status}</span>
            <span className="review-type">{REVIEWER[r.reviewer_type] ?? r.reviewer_type}</span>
          </div>
          <div>
            {r.proposed_label
              ? <>Proposed: <b>{labelOf(r.original_label)}</b> → <b>{labelOf(r.proposed_label)}</b></>
              : <>Note on <b>{labelOf(r.original_label)}</b> (no relabel proposed)</>}
          </div>
          <p className="review-reason">{r.reason}</p>
          <div className="insp-small">
            By {r.reviewer}{r.decided_by ? ` · decided by ${r.decided_by}` : ''}
            {r.decision_note ? ` — ${r.decision_note}` : ''}
          </div>
        </li>
      ))}
    </ul>
  )
}

export function QuoteList({ quotes, paperTitle, empty = 'No verified source sentences.' }) {
  if (!quotes?.length) return <p className="insp-empty">{empty}</p>
  return quotes.map((q) => <VerifiedQuote key={q.id} quote={q} paperTitle={paperTitle} />)
}

export function ModelBox({ title = 'Model assessment (Gemini) — not a verdict', children }) {
  return (
    <div className="model-box">
      <div className="model-box-head">{title}</div>
      {children}
    </div>
  )
}

export function Section({ title, children, note }) {
  return (
    <section className="insp-section">
      <h3>{title}{note ? <span className="insp-note">{note}</span> : null}</h3>
      {children}
    </section>
  )
}

/** `qualifier` ('review' | 'note' | null) qualifies a supportive label whose independence is not established,
 *  so "Supports" is never shown unqualified next to a review-needed status. */
export function RelPill({ label, insufficient, qualifier = null }) {
  const rel = RELATIONSHIPS[label]
  if (insufficient) return <span className="rel-pill rel-pill-warn">Insufficient evidence</span>
  return (
    <span className={`rel-pill${qualifier === 'review' ? ' rel-pill-review' : ''}`} style={{ '--rel': rel?.color ?? '#8e8ea0' }}>
      <span className="rel-swatch" data-line={rel?.line ?? 'solid'} aria-hidden="true" />
      {rel?.label ?? label}
      {qualifier === 'review' && <span className="rel-qual"> · independence not established</span>}
    </span>
  )
}

export function NodeChip({ n, kind, onClick }) {
  const text = n != null ? `Node ${n}` : 'Node'
  if (!onClick) return <span className={`node-chip ${kind ?? ''}`}>{text}</span>
  return <button type="button" className={`node-chip ${kind ?? ''}`} onClick={onClick}>{text}</button>
}

const show = (v) => (v == null || v === '' || v === NOT_REPORTED ? <i className="nr">not reported</i> : String(v))

/** condition_diff from the backend: per field "same", "not reported", or [value_a, value_b]. */
export function ConditionDiffTable({ diff, a, b }) {
  const fields = DIFF_FIELDS.filter((f) => f in (diff ?? {}))
  if (!fields.length) return <p className="insp-empty">No study conditions were compared.</p>
  return (
    <table className="cond-table">
      <thead><tr><th /><th>{a}</th><th>{b}</th></tr></thead>
      <tbody>
        {fields.map((f) => {
          const v = diff[f]
          if (Array.isArray(v)) {
            return (
              <tr key={f} className="differs">
                <th scope="row">{CONDITION_LABELS[f]}</th><td>{show(v[0])}</td><td>{show(v[1])}</td>
              </tr>
            )
          }
          return (
            <tr key={f}>
              <th scope="row">{CONDITION_LABELS[f]}</th>
              <td colSpan={2} className="muted">{v === 'same' ? 'Same in both' : show(v)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** All extracted conditions of one claim (model-extracted metadata). */
export function ConditionsList({ conditions }) {
  return (
    <dl className="cond-list">
      {Object.keys(CONDITION_LABELS).map((k) => (
        <div key={k}><dt>{CONDITION_LABELS[k]}</dt><dd>{show(conditions?.[k])}</dd></div>
      ))}
    </dl>
  )
}

export function AccessBadge({ level }) {
  return level === 'abstract' ? <span className="badge">Abstract only</span> : null
}

/** Opens the research assistant with this graph item attached. Disabled (never fake) without a chat backend. */
export function AskButton({ label, onAsk, context }) {
  return (
    <button type="button" className="btn btn-sm" disabled={!onAsk} onClick={() => onAsk?.(context)}
      title={onAsk ? 'Open the research assistant with this item attached' : 'The research assistant is not available here.'}>
      {label}
    </button>
  )
}
