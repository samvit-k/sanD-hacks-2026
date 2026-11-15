// Stage D5: independence status, classification reviews, quotation provenance and author lists in the UI.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { buildElements } from '../../graph/toElements.js'
import { BANNED_WORDS } from '../../graph/vocab.js'
import ChatMessage from '../assistant/ChatMessage.jsx'
import { ClaimView, PaperView } from './NodeViews.jsx'
import { quoteProvenance, VerifiedQuote } from './parts.jsx'
import RelationshipView from './RelationshipView.jsx'

afterEach(cleanup)

const SHARED = {
  status: 'shared_authors', shared: ['Chuhan Wu', 'Fangzhao Wu', 'Tao Qi', 'Xing Xie'], possible: [],
  basis_sentence_ids: ['nrms:p1_s1', 'mind:p1_s1'], independence_established: false, review_needed: true,
  summary: 'Independence not established — these papers share 4 authors (Chuhan Wu, Fangzhao Wu, Tao Qi, Xing Xie). '
    + 'Shared authorship links the studies; it does not by itself mean the same data or experiments.',
}
const claim = (id, paper) => ({ id, paper_id: paper, paper_title: paper.toUpperCase(), access_level: 'full', text: `Claim ${id}`,
  conditions: {}, quotes: [{ id: `${paper}:p4_s22`, paper_id: paper, page: 4, section: 'results', text: 'A finding.',
    kind: null, text_check: null, artifacts: [] }] })
const rel = (over = {}) => ({
  id: 7, label: 'supports', similarity: 0.93, insufficient_evidence: false, explanation: 'Both report NRMS performs best.',
  key_differences: [], condition_diff: {}, claim_a: claim(1, 'nrms'), claim_b: claim(7, 'mind'),
  cited: { a: [], b: [] }, reviews: [], model_label: null, comparability: null, label_note: null,
  independence: { ...SHARED, level: 'review', headline: 'Independence not established — review needed' }, ...over,
})
const graph = { question: 'Q?', papers: {}, relationships: {}, claims: {} }
const refs = { 'claim:1': 2, 'claim:7': 10, 'claim:9': 12 }
const noBannedWords = (text) => {
  for (const w of BANNED_WORDS) expect(text.toLowerCase()).not.toMatch(new RegExp(`\\b${w}\\b`))
}

describe('relationship independence', () => {
  it('a supporting relationship between papers that share authors is never shown as unqualified support', () => {
    render(<RelationshipView rel={rel()} graph={graph} refs={refs} onNavigate={() => {}} />)
    const notice = screen.getByTestId('independence')
    expect(notice.textContent).toMatch(/Independence not established — review needed/)
    expect(notice.textContent).toMatch(/share 4 authors \(Chuhan Wu, Fangzhao Wu, Tao Qi, Xing Xie\)/)
    expect(notice.textContent).toMatch(/nrms:p1_s1/) // the basis is cited
    expect(document.querySelector('.rel-pill').textContent).toBe('Supports · independence not established')
    const html = document.body.innerHTML
    expect(html.indexOf('data-testid="independence"')).toBeLessThan(html.indexOf('Both report NRMS performs best.'))
    noBannedWords(notice.textContent)
  })

  it('not-comparable relationships keep author overlap as context only', () => {
    render(<RelationshipView rel={rel({ label: 'not_comparable', independence: { ...SHARED, level: null, headline: null } })}
      graph={graph} refs={refs} onNavigate={() => {}} />)
    expect(screen.getByTestId('independence').textContent).not.toMatch(/review needed/)
    expect(document.querySelector('.rel-pill').textContent).toBe('Not comparable')
  })

  it('shows proposed reclassifications as model recommendations, distinct from human decisions, without applying them', () => {
    const reviews = [
      { id: 1, status: 'proposed', reviewer_type: 'model', reviewer: 'Claude (AI coding assistant)', original_label: 'supports',
        proposed_label: 'not_comparable', reason: 'The findings test different hypotheses.' },
      { id: 2, status: 'accepted', reviewer_type: 'human', reviewer: 'Dr. A', decided_by: 'Dr. A', original_label: 'supports',
        proposed_label: 'not_comparable', reason: 'Agreed.' },
    ]
    render(<RelationshipView rel={rel({ id: 9, claim_b: claim(9, 'mind'), reviews })} graph={graph} refs={refs} onNavigate={() => {}} />)
    const list = screen.getByTestId('reviews')
    expect(list.textContent).toMatch(/Proposed — not applied/)
    expect(list.textContent).toMatch(/AI model recommendation — not a human decision/)
    expect(list.textContent).toMatch(/Human review/)
    expect(list.textContent).toMatch(/Supports → Not comparable/)
    expect(screen.getByText('Under review')).toBeTruthy()
    expect(document.querySelector('.rel-pill').textContent).toMatch(/^Supports/) // the original label is still shown
  })

  it('explains a label ResearchLens adjusted from Gemini’s own structured answer', () => {
    render(<RelationshipView rel={rel({ label: 'not_comparable', model_label: 'supports', comparability: 'thematic_only',
      label_note: 'Thematic similarity is not support.', independence: null })} graph={graph} refs={refs} onNavigate={() => {}} />)
    const note = screen.getByTestId('label-note')
    expect(note.textContent).toMatch(/Thematic similarity is not support/)
    expect(note.textContent).toMatch(/Gemini’s label: Supports · comparability: thematic_only/)
  })
})

describe('impact independence', () => {
  it('a "strengthens" impact without established independence is qualified, with the reason shown', () => {
    const impact = { impacts: [{ id: 6, impact: 'strengthens', rationale: 'Comparable gains.', insufficient_evidence: false,
      new_claim: { id: 9 }, prior_claim: { id: 1 }, reviews: [],
      independence: { ...SHARED, level: 'review', headline: 'Independence not established — review needed' } }] }
    render(<ClaimView claim={claim(1, 'nrms')} graph={graph} refs={refs} onNavigate={() => {}} impact={impact} />)
    const item = screen.getByTestId('impact-item')
    expect(item.querySelector('.impact-pill').textContent).toBe('Strengthens · independence not established')
    expect(item.textContent).toMatch(/review needed/)
  })
})

describe('chat answers re-checked for independence', () => {
  it('a stored answer that overstates confirmation gets a prominent status before the unchanged text', () => {
    const message = { id: 2, role: 'assistant', content: 'The MIND study confirms this finding.', cited: [], references: {},
      insufficient_evidence: false, guardrail: null,
      integrity: { headline: 'Independence not established — review needed', detail: SHARED.summary,
        note: 'Checked by ResearchLens when displayed; the answer text below is unchanged.' } }
    render(<ChatMessage message={message} />)
    const banner = screen.getByTestId('chat-integrity')
    expect(banner.textContent).toMatch(/review needed/)
    expect(banner.textContent).toMatch(/share 4 authors/)
    const html = screen.getByTestId('assistant-message').innerHTML
    expect(html.indexOf('chat-integrity')).toBeLessThan(html.indexOf('The MIND study confirms this finding.'))
    noBannedWords(banner.textContent)
  })
})

describe('quotation provenance', () => {
  const q = (over) => ({ id: 'p:p7_s16', paper_id: 'p', page: 7, section: 'results', text: 'x', kind: 'body',
    text_check: 'single_block', artifacts: [], ...over })
  it('distinguishes single-block text, reconstructed text and legacy quotes with layout artifacts', () => {
    expect(quoteProvenance(q({})).tone).toBe('ok')
    expect(quoteProvenance(q({ text_check: 'joined_blocks' })).text).toMatch(/Joined across PDF layout blocks — verify the wording on p\. 7/)
    expect(quoteProvenance(q({ text_check: null, kind: null, artifacts: ['word_fragment', 'caption_inside'] })).text)
      .toMatch(/May contain PDF layout artifacts \(a split word, caption text\) — verify on p\. 7/)
    expect(quoteProvenance(q({ text_check: null, kind: null }))).toBeNull() // legacy and nothing suspicious
  })
  it('labels captions, tables and footnotes', () => {
    render(<VerifiedQuote quote={q({ kind: 'caption', text: 'Table 2: The results.' })} />)
    expect(document.querySelector('figcaption').textContent).toMatch(/Caption/)
  })
})

describe('author lists', () => {
  const paper = (authors) => ({ id: 'p', title: 'T', access_level: 'full', authors })
  it('shows parsed authors with their source sentence, and unknown lists as unknown', () => {
    render(<PaperView paper={paper({ status: 'parsed', names: ['Chuhan Wu', 'Xing Xie'], source_sentence_id: 'p:p1_s1' })}
      graph={graph} refs={{}} onNavigate={() => {}} />)
    expect(screen.getByTestId('authors').textContent).toMatch(/Chuhan Wu, Xing Xie/)
    expect(screen.getByTestId('authors').textContent).toMatch(/p:p1_s1/)
    cleanup()
    render(<PaperView paper={paper({ status: 'unknown', names: [], reason: 'author and affiliation run together' })}
      graph={graph} refs={{}} onNavigate={() => {}} />)
    expect(screen.getByTestId('authors').textContent).toMatch(/Could not be read reliably.*unknown/)
  })
})

describe('graph elements', () => {
  it('marks evidence edges whose independence is not established, and edges under review', () => {
    const g = {
      nodes: [{ data: { id: 'q', type: 'question', label: 'Q' } },
        { data: { id: 'claim:1', type: 'claim', label: 'a', claim_id: 1, paper_id: 'p' } },
        { data: { id: 'claim:2', type: 'claim', label: 'b', claim_id: 2, paper_id: 'r' } }],
      edges: [{ data: { id: 'rel:7', source: 'claim:1', target: 'claim:2', type: 'relationship', label: 'supports',
        relationship_id: 7, insufficient_evidence: false, independence: 'review', under_review: true } }],
      papers: {}, claims: {}, relationships: {},
    }
    const edge = buildElements(g).edges.find((e) => e.data.id === 'rel:7')
    expect(edge.classes.split(' ')).toEqual(expect.arrayContaining(['evidence', 'rel-supports', 'indep-review', 'under-review']))
  })
})
