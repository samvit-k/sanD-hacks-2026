import { useEffect, useRef, useState } from 'react'
import { LEGEND_EVENT } from '../graph/viewport.js'
import { INDEPENDENCE, INSUFFICIENT, NODE_KINDS, RELATIONSHIPS, RELATIONSHIP_ORDER, REVIEW, STRUCTURAL } from '../graph/vocab.js'

function LineSwatch({ color, line, width = 1.5, opacity = 1 }) {
  const dash = line === 'dashed' ? '5 4' : line === 'dotted' ? '1.5 3' : undefined
  return (
    <svg className="line-swatch" viewBox="0 0 28 8" aria-hidden="true">
      <line x1="1" y1="4" x2="27" y2="4" stroke={color} strokeWidth={width} strokeDasharray={dash}
        strokeLinecap="round" opacity={opacity} />
    </svg>
  )
}

const NODE_ORDER = ['question', 'paper', 'paper_new', 'paper_abstract', 'claim', 'claim_new', 'discovery']
// Legend labels are shown in Title Case; the shared vocabulary (used in pills elsewhere) is left unchanged.
export const titleCase = (text) => text.replace(/(^|[\s(-])([a-z])/g, (_, sep, ch) => sep + ch.toUpperCase())

const SHORT = {
  question: 'Question', paper: 'Paper', paper_new: 'Evaluated paper', paper_abstract: 'Abstract only',
  claim: 'Claim', claim_new: 'New claim', discovery: 'Lead (not analyzed)',
}

// Compact floating legend card (BioOKF bottom-left "NODE TYPES … hide").
const startsOpen = () => typeof window === 'undefined' || !window.matchMedia?.('(max-width: 1100px)').matches

export default function Legend() {
  const [open, setOpen] = useState(startsOpen) // collapsed to a pill on narrow screens so it never covers the graph
  const mounted = useRef(false)
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return }
    window.dispatchEvent(new Event(LEGEND_EVENT)) // after the DOM reflects the new state
  }, [open])
  if (!open) {
    return <button type="button" className="legend-pill" onClick={() => setOpen(true)}>Legend</button>
  }
  return (
    <section className="legend" aria-label="Legend">
      <h4>
        Legend
        <button type="button" className="tg" onClick={() => setOpen(false)}>Hide</button>
      </h4>
      <div className="legend-sub">Nodes</div>
      <div className="legend-grid">
        {NODE_ORDER.map((k) => (
          <span className="legend-item" key={k} title={NODE_KINDS[k].label}>
            {k === 'question' ? <img className="legend-logo" src="/logo.svg" alt="" aria-hidden="true" /> : <span
              className={`node-dot${NODE_KINDS[k].dashed ? ' dashed' : ''}`}
              style={{ background: NODE_KINDS[k].color, boxShadow: NODE_KINDS[k].ring ? `0 0 0 2px ${NODE_KINDS[k].ring}` : undefined }}
            />}
            {titleCase(SHORT[k])}
          </span>
        ))}
      </div>
      <div className="legend-sub">Evidence Relationships <span className="legend-note">Model-Assessed</span></div>
      <div className="legend-grid">
        {RELATIONSHIP_ORDER.map((k) => (
          <span className="legend-item" key={k} title={RELATIONSHIPS[k].help}>
            <LineSwatch color={RELATIONSHIPS[k].color} line={RELATIONSHIPS[k].line} width={RELATIONSHIPS[k].width}
              opacity={RELATIONSHIPS[k].opacity ?? 1} />
            {titleCase(RELATIONSHIPS[k].label)}
          </span>
        ))}
        <span className="legend-item" title={INSUFFICIENT.help}>
          <LineSwatch color={RELATIONSHIPS.unresolved.color} line="dotted" width={1.6} opacity={0.5} />
          {titleCase(INSUFFICIENT.label)}
        </span>
        <span className="legend-item" title={INDEPENDENCE.help}>
          <span className="indep-swatch" aria-hidden="true"><LineSwatch color={RELATIONSHIPS.supports.color} line="solid" width={1.5} /></span>
          Shared Authors
        </span>
        <span className="legend-item" title={REVIEW.help}>
          <span className="review-swatch" aria-hidden="true">Review</span>
          Under Review
        </span>
      </div>
      <div className="legend-sub">Structure</div>
      <div className="legend-grid">
        <span className="legend-item" title={STRUCTURAL.question_paper.help}>
          <LineSwatch color="#1d232a" line="dotted" width={1} opacity={0.45} />
          {titleCase(STRUCTURAL.question_paper.label)}
        </span>
        <span className="legend-item" title={STRUCTURAL.has_claim.help}>
          <LineSwatch color="#1d232a" line="solid" width={1} opacity={0.35} />
          {titleCase(STRUCTURAL.has_claim.label)}
        </span>
      </div>
    </section>
  )
}