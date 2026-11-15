import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AssistantPanel from '../components/assistant/AssistantPanel.jsx'
import { PaperView } from '../components/inspector/NodeViews.jsx'
import { describeExtraction, OUTCOME } from './extraction.js'

afterEach(cleanup)

const Q = 'How does GraphRAG affect the performance of NLP summarization in political documents?'
const NONE = { returned: 0, kept: 0, outcome: OUTCOME.none, question: Q, model_note: 'The paper studies news recommendation.' }
const REJECTED = { returned: 3, kept: 0, rejected_invalid_citations: 3, outcome: OUTCOME.rejected, question: Q, model_note: null }

describe('describeExtraction', () => {
  it('distinguishes no candidates from failed verification, and says nothing for success or unknown', () => {
    expect(describeExtraction(NONE).title).toBe('No relevant findings identified for this question')
    expect(describeExtraction(REJECTED).title).toBe('3 candidate findings failed citation verification')
    expect(describeExtraction({ outcome: OUTCOME.claims, kept: 2 })).toBeNull()
    expect(describeExtraction(null)).toBeNull() // not recorded: no invented status
  })

  it('never claims the paper contains no relevant findings', () => {
    const text = JSON.stringify(describeExtraction(NONE))
    expect(text).not.toMatch(/contains no|has no relevant|no relevant findings exist/i)
  })
})

const uploads = (items) => ({ items, busy: false, add: vi.fn(() => ({ accepted: [], rejected: [] })), retry: vi.fn(),
  canRetry: () => false, checkAgain: vi.fn(), retryRefresh: vi.fn(), dismiss: vi.fn() })
const chat = { messages: [], pending: null, error: null, send: vi.fn(), retry: vi.fn() }
const panel = (props) => render(<AssistantPanel open onClose={() => {}} chat={chat} canUpload hasLibrary chatEnabled
  uploadBlockedReason="" chatBlockedReason="" context={null} contextLabel={() => ''} onClearContext={() => {}}
  onReference={() => {}} focusTick={0} {...props} />)

describe('upload results', () => {
  it('a zero-claim upload says "Analyzed — no relevant findings identified", not "Done · 0 claims"', () => {
    panel({ uploads: uploads([{ key: 'k', name: 'UserRAG_4.pdf', mode: 'library', status: 'done',
      result: { mode: 'library', claims_kept: 0, relationships_created: 0, extraction: NONE } }]) })
    const row = screen.getByRole('listitem')
    expect(row.textContent).toMatch(/Analyzed/)
    expect(row.textContent).toMatch(/No relevant findings identified for this question/)
    expect(row.textContent).not.toMatch(/Done/)
    expect(row.textContent).not.toMatch(/0 claims · 0 new relationships/)
  })

  it('a successful upload is unchanged', () => {
    panel({ uploads: uploads([{ key: 'k', name: 'a.pdf', mode: 'library', status: 'done',
      result: { mode: 'library', claims_kept: 3, relationships_created: 2, extraction: { outcome: OUTCOME.claims } } }]) })
    expect(screen.getByRole('listitem').textContent).toMatch(/Done.*3 claims · 2 new relationships/)
  })

  it('the chat explains why answers are withheld when a project has no verified findings', () => {
    panel({ uploads: uploads([]), evidenceNote: 'This project has papers but no verified findings yet.' })
    expect(screen.getByRole('note').textContent).toMatch(/no verified findings/)
  })
})

describe('paper inspector', () => {
  const graph = { claims: {}, relationships: {}, papers: {} }
  const paper = (extraction) => ({ id: 'p', title: 'Neural News Recommendation', access_level: 'full', extraction })

  it('shows the outcome, the question it was judged against, and Gemini\'s note as a model assessment', () => {
    render(<PaperView paper={paper(NONE)} graph={graph} refs={{ 'paper:p': 3 }} onNavigate={() => {}} />)
    const card = screen.getByTestId('extraction-outcome')
    expect(card.textContent).toMatch(/No relevant findings identified for this question/)
    expect(card.textContent).toContain(Q)
    const note = screen.getByText('The paper studies news recommendation.')
    expect(note.closest('.model-box')).toBeTruthy()
    expect(note.closest('.vq')).toBeNull() // never shown as a verified quotation
  })

  it('shows failed verification differently from "no findings"', () => {
    render(<PaperView paper={paper(REJECTED)} graph={graph} refs={{}} onNavigate={() => {}} />)
    expect(screen.getByTestId('extraction-outcome').textContent).toMatch(/failed citation verification/)
  })
})
