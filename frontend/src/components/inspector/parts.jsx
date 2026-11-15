// Shared inspector building blocks. Two visual languages are kept strictly apart:
//  - VerifiedQuote: verbatim sentences rebuilt by the server from the stored PDF text.
//  - ModelBox: anything Gemini produced (claim paraphrases, explanations, rationales).
import { CONDITION_LABELS, DIFF_FIELDS, RELATIONSHIPS, SECTION_LABELS } from '../../graph/vocab.js'

const NOT_REPORTED = 'not reported'

export function VerifiedQuote({ quote, paperTitle }) {
  return (
    <figure className="vq">
      <blockquote>{quote.text}</blockquote>
      <figcaption>
        <span className="vq-ok" aria-hidden="true">✓</span> Verified source sentence · p. {quote.page}
        {quote.section && quote.section !== 'front' ? ` · ${SECTION_LABELS[quote.section] ?? quote.section}` : ''}
        {' · '}<code title="Sentence ID (citation)">{quote.id}</code>
        {paperTitle ? <div className="vq-paper">{paperTitle}</div> : null}
      </figcaption>
    </figure>
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

export function RelPill({ label, insufficient }) {
  const rel = RELATIONSHIPS[label]
  if (insufficient) return <span className="rel-pill rel-pill-warn">Insufficient evidence</span>
  return (
    <span className="rel-pill" style={{ '--rel': rel?.color ?? '#8e8ea0' }}>
      <span className="rel-swatch" data-line={rel?.line ?? 'solid'} aria-hidden="true" />
      {rel?.label ?? label}
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
