import { INSUFFICIENT, RELATIONSHIPS } from '../../graph/vocab.js'
import { AccessBadge, AskButton, ConditionDiffTable, ModelBox, NodeChip, QuoteList, RelPill, Section } from './parts.jsx'

const fmt = (x) => (typeof x === 'number' ? x.toFixed(2) : '—')

function ClaimColumn({ side, claim, cited, n, insufficient, onNavigate }) {
  // Prefer the sentences cited for this judgment; otherwise the claim's own verified sentences.
  const useCited = !insufficient && cited?.length > 0
  return (
    <div className="rel-col">
      <div className="rel-col-head">
        <span className="rel-side">Claim {side}</span>
        <NodeChip n={n} kind="claim" onClick={() => onNavigate({ kind: 'claim', id: claim.id })} />
      </div>
      <button type="button" className="rel-paper link-btn" onClick={() => onNavigate({ kind: 'paper', id: claim.paper_id })}>
        {claim.paper_title}
      </button>
      <AccessBadge level={claim.access_level} />
      <ModelBox title="Model-extracted claim">
        <p>{claim.text}</p>
      </ModelBox>
      <div className="rel-quotes-head">{useCited ? 'Cited for this judgment' : 'Claim’s source sentences'}</div>
      <QuoteList quotes={useCited ? cited : claim.quotes} />
    </div>
  )
}

export default function RelationshipView({ rel, graph, refs, onNavigate, onAsk }) {
  const insufficient = rel.insufficient_evidence
  const nA = refs[`claim:${rel.claim_a.id}`]
  const nB = refs[`claim:${rel.claim_b.id}`]
  const relA = graph.papers?.[rel.claim_a.paper_id]?.relevance
  const relB = graph.papers?.[rel.claim_b.paper_id]?.relevance
  const help = insufficient ? INSUFFICIENT.help : RELATIONSHIPS[rel.label]?.help
  return (
    <>
      <div className="insp-kicker">Evidence relationship</div>
      <h2 className="insp-title">Node {nA ?? '?'} ↔ Node {nB ?? '?'}</h2>
      <div className="insp-pillrow"><RelPill label={rel.label} insufficient={insufficient} /></div>
      {help && <p className="insp-help">{help}</p>}

      {insufficient ? (
        <div className="warn-card" role="note">
          <b>{INSUFFICIENT.label}.</b> The model’s cited sentences failed verification against the stored
          papers, so its judgment is not shown. The two claims below are shown with their own verified sentences.
        </div>
      ) : (
        <Section title="Why they are connected">
          <ModelBox>
            <p>{rel.explanation || 'No explanation was returned.'}</p>
            {rel.key_differences?.length > 0 && (
              <>
                <div className="model-sub">Key differences</div>
                <ul>{rel.key_differences.map((d, i) => <li key={i}>{d}</li>)}</ul>
              </>
            )}
          </ModelBox>
        </Section>
      )}

      <Section title="Relevance to the central question">
        <p className="insp-q">“{graph.question || 'No research question set'}”</p>
        <p className="insp-small">
          Both claims were extracted as findings bearing on this question. Their papers’ embedding similarity to it:
          {' '}<b>{fmt(relA)}</b> and <b>{fmt(relB)}</b> — a navigation aid, not a measure of truth or quality.
        </p>
      </Section>

      <Section title="Claims and verified sources">
        <div className="rel-cols">
          <ClaimColumn side="A" claim={rel.claim_a} cited={rel.cited?.a} n={nA} insufficient={insufficient} onNavigate={onNavigate} />
          <ClaimColumn side="B" claim={rel.claim_b} cited={rel.cited?.b} n={nB} insufficient={insufficient} onNavigate={onNavigate} />
        </div>
      </Section>

      <Section title="Study conditions" note="compared by code; differences inform, never decide">
        <ConditionDiffTable diff={rel.condition_diff} a={`Node ${nA ?? 'A'}`} b={`Node ${nB ?? 'B'}`} />
      </Section>

      <div className="insp-foot">
        <span className="insp-small">
          Embedding similarity {fmt(rel.similarity)} — used only to select this pair for comparison.
        </span>
        <AskButton label="Ask about this connection" onAsk={onAsk} context={{ kind: 'relationship', id: rel.id }} />
      </div>
    </>
  )
}
