// Inspector views for nodes: claim, paper, central question, discovery lead.
import { shorten } from '../../graph/toElements.js'
import { AccessBadge, ConditionsList, ModelBox, NodeChip, QuoteList, RelPill, Section } from './parts.jsx'

const fmt = (x) => (typeof x === 'number' ? x.toFixed(2) : '—')

export function ClaimView({ claim, graph, refs, onNavigate }) {
  const n = refs[`claim:${claim.id}`]
  const rels = Object.values(graph.relationships ?? {}).filter(
    (r) => r.claim_a.id === claim.id || r.claim_b.id === claim.id,
  )
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
      <Section title={`Evidence relationships (${rels.length})`}>
        {rels.length === 0 ? <p className="insp-empty">Not matched with claims from other papers.</p> : (
          <ul className="insp-list">
            {rels.map((r) => {
              const other = r.claim_a.id === claim.id ? r.claim_b : r.claim_a
              return (
                <li key={r.id}>
                  <button type="button" className="insp-row" onClick={() => onNavigate({ kind: 'relationship', id: r.id })}>
                    <RelPill label={r.label} insufficient={r.insufficient_evidence} />
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

export function PaperView({ paper, graph, refs, onNavigate }) {
  const n = refs[`paper:${paper.id}`]
  const claims = Object.values(graph.claims ?? {}).filter((c) => c.paper_id === paper.id)
  const link = paper.doi ? `https://doi.org/${paper.doi}` : paper.url
  return (
    <>
      <div className="insp-kicker">{paper.is_new ? 'Evaluated paper' : 'Paper'} · Node {n ?? '?'}</div>
      <h2 className="insp-title">{paper.title}</h2>
      <div className="insp-pillrow">
        {paper.is_new && <span className="badge badge-new">Evaluated (new)</span>}
        <AccessBadge level={paper.access_level} />
        {paper.access_level !== 'abstract' && <span className="badge">Full text</span>}
      </div>
      <dl className="cond-list">
        <div><dt>Year</dt><dd>{paper.year ?? <i className="nr">not recorded</i>}</dd></div>
        <div><dt>DOI / link</dt><dd>{link ? <a href={link} target="_blank" rel="noreferrer">{paper.doi ?? link}</a> : <i className="nr">not recorded</i>}</dd></div>
        <div><dt>Licence</dt><dd>{paper.license ?? <i className="nr">not recorded</i>}</dd></div>
        <div>
          <dt>Similarity to question</dt>
          <dd>{fmt(paper.relevance)} <span className="insp-small">— navigation aid, not a truth score</span></dd>
        </div>
      </dl>
      <Section title={`Extracted claims (${claims.length})`}>
        {claims.length === 0 ? <p className="insp-empty">No claims extracted.</p> : (
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

export function QuestionView({ graph, workspace }) {
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
