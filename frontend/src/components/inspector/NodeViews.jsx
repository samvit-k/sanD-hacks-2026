// Inspector views for nodes: claim, paper, central question, discovery lead.
import { describeExtraction } from '../../graph/extraction.js'
import { shorten } from '../../graph/toElements.js'
import { IMPACTS } from '../../graph/vocab.js'
import { AccessBadge, AskButton, ConditionsList, IndependenceNotice, ModelBox, NodeChip, QuoteList, RelPill, ReviewList,
  Section } from './parts.jsx'

const fmt = (x) => (typeof x === 'number' ? x.toFixed(2) : '—')

/** Impact label, qualified when the label leans on independence that has not been established. */
export function impactLabel(i) {
  const base = IMPACTS[i.impact]?.label ?? i.impact
  if (i.insufficient_evidence) return 'Insufficient evidence'
  if (i.independence?.level === 'review') return `${base} · independence not established`
  if (i.independence?.level === 'note') return `${base} · independence not verified`
  return base
}

function ImpactSection({ items, refs }) {
  if (!items.length) return null
  return (
    <Section title="Impact of the latest evaluated paper" note="model assessment">
      {items.map((i) => (
        <div key={i.id} className="impact-item" data-testid="impact-item">
          <div className="insp-pillrow">
            <span className={`impact-pill impact-${i.impact}${i.independence?.level === 'review' ? ' review' : ''}`}>
              {impactLabel(i)}
            </span>
            <span className="insp-small">new Node {refs[`claim:${i.new_claim.id}`] ?? '?'} → earlier Node {refs[`claim:${i.prior_claim.id}`] ?? '?'}</span>
          </div>
          <IndependenceNotice independence={i.independence} />
          {!i.insufficient_evidence && <ModelBox><p>{i.rationale}</p></ModelBox>}
          <ReviewList reviews={i.reviews} labelOf={(l) => IMPACTS[l]?.label ?? l} />
        </div>
      ))}
    </Section>
  )
}

export function ClaimView({ claim, graph, refs, onNavigate, onAsk, impact = null }) {
  const n = refs[`claim:${claim.id}`]
  const rels = Object.values(graph.relationships ?? {}).filter(
    (r) => r.claim_a.id === claim.id || r.claim_b.id === claim.id,
  )
  const impacts = (impact?.impacts ?? []).filter((i) => i.prior_claim.id === claim.id || i.new_claim.id === claim.id)
  return (
    <>
      <div className="insp-kicker">Claim · Node {n ?? '?'}</div>
      <button type="button" className="insp-paperlink link-btn" onClick={() => onNavigate({ kind: 'paper', id: claim.paper_id })}>
        {claim.paper_title}
      </button>
      <AccessBadge level={claim.access_level} />
      <ModelBox title="Model-extracted claim (paraphrase — see sources below)">
        <p className="claim-text">{claim.text}</p>
      </ModelBox>
      <Section title="Verified source sentences">
        <QuoteList quotes={claim.quotes} />
      </Section>
      <Section title="Study conditions" note="model-extracted">
        <ConditionsList conditions={claim.conditions} />
      </Section>
      <div className="insp-ask"><AskButton label="Ask about this claim" onAsk={onAsk} context={{ kind: 'claim', id: claim.id }} /></div>
      <ImpactSection items={impacts} refs={refs} />
      <Section title={`Evidence relationships (${rels.length})`}>
        {rels.length === 0 ? <p className="insp-empty">Not matched with claims from other papers.</p> : (
          <ul className="insp-list">
            {rels.map((r) => {
              const other = r.claim_a.id === claim.id ? r.claim_b : r.claim_a
              return (
                <li key={r.id}>
                  <button type="button" className="insp-row" onClick={() => onNavigate({ kind: 'relationship', id: r.id })}>
                    <RelPill label={r.label} insufficient={r.insufficient_evidence} qualifier={r.independence?.level} />
                    <span>with <b>Node {refs[`claim:${other.id}`] ?? '?'}</b> · {shorten(other.text, 70)}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </Section>
    </>
  )
}

function AuthorList({ authors }) {
  if (!authors) return null
  return (
    <div className="author-list" data-testid="authors">
      <div className="insp-small"><b>Authors</b> — parsed from the paper’s first-page text
        {authors.source_sentence_id && <> (<code>{authors.source_sentence_id}</code>)</>}</div>
      {authors.status === 'parsed'
        ? <p>{authors.names.join(', ')}</p>
        : <p className="insp-empty">Could not be read reliably{authors.reason ? `: ${authors.reason}` : ''}. Shared authorship with other papers is therefore unknown.</p>}
    </div>
  )
}

export function PaperView({ paper, graph, refs, onNavigate, onAsk }) {
  const n = refs[`paper:${paper.id}`]
  const claims = Object.values(graph.claims ?? {}).filter((c) => c.paper_id === paper.id)
  const link = paper.doi ? `https://doi.org/${paper.doi}` : paper.url
  const outcome = describeExtraction(paper.extraction)
  return (
    <>
      <div className="insp-kicker">{paper.is_new ? 'Evaluated paper' : 'Paper'} · Node {n ?? '?'}</div>
      <h2 className="insp-title">{paper.title}</h2>
      <div className="insp-pillrow">
        {paper.is_new && <span className="badge badge-new">Evaluated (new)</span>}
        <AccessBadge level={paper.access_level} />
        {paper.access_level !== 'abstract' && <span className="badge">Full text</span>}
        {outcome && <span className="badge badge-pending">{outcome.badge}</span>}
      </div>
      {outcome && (
        <div className={`warn-card ${outcome.kind === 'none' ? 'soft' : ''}`} role="note" data-testid="extraction-outcome">
          <b>{outcome.title}.</b> {outcome.detail}
          {outcome.question && <div className="insp-small">Question at the time: “{outcome.question}”</div>}
        </div>
      )}
      {outcome?.note && (
        <ModelBox title="Gemini's note — model assessment, not evidence"><p>{outcome.note}</p></ModelBox>
      )}
      <AuthorList authors={paper.authors} />
      <dl className="cond-list">
        <div><dt>Year</dt><dd>{paper.year ?? <i className="nr">not recorded</i>}</dd></div>
        <div><dt>DOI / link</dt><dd>{link ? <a href={link} target="_blank" rel="noreferrer">{paper.doi ?? link}</a> : <i className="nr">not recorded</i>}</dd></div>
        <div><dt>Licence</dt><dd>{paper.license ?? <i className="nr">not recorded</i>}</dd></div>
        <div>
          <dt>Similarity to question</dt>
          <dd>{fmt(paper.relevance)} <span className="insp-small">— navigation aid, not a truth score</span></dd>
        </div>
      </dl>
      <div className="insp-ask"><AskButton label="Ask about this paper" onAsk={onAsk} context={{ kind: 'paper', id: paper.id }} /></div>
      <Section title={`Extracted claims (${claims.length})`}>
        {claims.length === 0 ? <p className="insp-empty">{outcome ? outcome.title : 'No claims extracted.'}</p> : (
          <ul className="insp-list">
            {claims.map((c) => (
              <li key={c.id}>
                <button type="button" className="insp-row" onClick={() => onNavigate({ kind: 'claim', id: c.id })}>
                  <NodeChip n={refs[`claim:${c.id}`]} kind="claim" />
                  <span>{shorten(c.text, 110)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  )
}

export function QuestionView({ graph, workspace, onAsk, onEditQuestion }) {
  const papers = Object.keys(graph.papers ?? {}).length
  const claims = Object.keys(graph.claims ?? {}).length
  return (
    <>
      <div className="insp-kicker">Central Question</div>
      <h2 className="insp-title insp-question">{graph.question || 'No research question set'}</h2>
      <p className="insp-small">
        This question grounds claim extraction, comparison and impact analysis for {papers} papers and {claims} claims.
        {workspace?.source === 'default' ? ' It is the demo default from the server configuration.' : ''}
      </p>
      <div className="insp-ask">
        <AskButton label="Ask about the central question" onAsk={onAsk} context={{ kind: 'question', id: 'q' }} />
        {onEditQuestion && <button type="button" className="btn btn-sm" onClick={onEditQuestion}>Change Question</button>}
      </div>
    </>
  )
}

export function DiscoveryView({ lead, discovery }) {
  const link = lead.doi ? `https://doi.org/${lead.doi}` : lead.oa_url
  return (
    <>
      <div className="insp-kicker">Related research lead · not analyzed</div>
      <h2 className="insp-title">{lead.title || 'Untitled'}</h2>
      <div className="warn-card soft" role="note">
        A discovery lead from {discovery?.source === 'brightdata' ? 'Bright Data (Google Scholar)' : 'OpenAlex'}
        {discovery?.fallback_reason ? ` (${discovery.fallback_reason})` : ''}. It is not evidence and has not been analyzed.
      </div>
      <dl className="cond-list">
        <div><dt>Year</dt><dd>{lead.year ?? <i className="nr">not recorded</i>}</dd></div>
        <div><dt>Link</dt><dd>{link ? <a href={link} target="_blank" rel="noreferrer">{lead.doi ?? link}</a> : <i className="nr">not recorded</i>}</dd></div>
      </dl>
      {lead.abstract && (
        <Section title="Abstract" note="metadata only">
          <p className="insp-small">{lead.abstract}</p>
        </Section>
      )}
    </>
  )
}