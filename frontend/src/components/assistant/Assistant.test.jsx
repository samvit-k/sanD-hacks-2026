import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nodeRefs } from '../../graph/toElements.js'
import Inspector from '../inspector/Inspector.jsx'
import AssistantPanel from './AssistantPanel.jsx'
import ChatMessage from './ChatMessage.jsx'

afterEach(cleanup)

const QUOTE = { id: 'paper-a:p3_s17', paper_id: 'paper-a', page: 3, section: 'results', text: 'Scores rose 25 percent.', paper_title: 'Paper A' }
const ANSWER = {
  id: 2, role: 'assistant', content: 'According to [[claim:3]] and [[rel:5]], this bears on [[q]].',
  insufficient_evidence: false, guardrail: null, cited: [QUOTE],
  references: {
    'claim:3': { kind: 'claim', id: 3, node_id: 'claim:3', ref: 7, missing: false },
    'rel:5': { kind: 'relationship', id: 5, node_id: 'rel:5', refs: [3, 6], missing: false },
    q: { kind: 'question', id: 'q', node_id: 'q', missing: false },
  },
}

describe('ChatMessage', () => {
  it('renders node references as clickable chips that navigate the graph', () => {
    const onReference = vi.fn()
    render(<ChatMessage message={ANSWER} onReference={onReference} />)
    fireEvent.click(screen.getByRole('button', { name: 'Node 7' }))
    expect(onReference).toHaveBeenCalledWith(ANSWER.references['claim:3'])
    fireEvent.click(screen.getByRole('button', { name: 'Node 3 ↔ Node 6' }))
    expect(onReference).toHaveBeenLastCalledWith(ANSWER.references['rel:5'])
    expect(screen.getByRole('button', { name: 'Central Question' })).toBeTruthy()
  })

  it('keeps verified quotations separate from the model interpretation', () => {
    render(<ChatMessage message={ANSWER} />)
    const quote = screen.getByText(QUOTE.text)
    expect(quote.closest('.vq')).toBeTruthy()
    expect(quote.closest('.model-box')).toBeNull()
    expect(quote.closest('.vq').textContent).toContain(QUOTE.id)
    expect(screen.getByText(/interpretation, not a verdict/).closest('.model-box')).toBeTruthy()
  })

  it('shows a withheld answer as a warning, never as model prose or with quotes', () => {
    render(<ChatMessage message={{ ...ANSWER, guardrail: 'uncited_findings', content: 'The answer was withheld.', cited: [] }} />)
    expect(screen.getByText(/Answer withheld/).closest('.warn-card')).toBeTruthy()
    expect(document.querySelector('.model-box')).toBeNull()
    expect(document.querySelector('.vq')).toBeNull()
  })

  it('renders status notes as app notices, not assistant output', () => {
    render(<ChatMessage message={{ id: 'note-1', role: 'status', content: 'Added “X” to the library.' }} />)
    expect(screen.getByRole('status').className).toContain('chat-status')
    expect(screen.queryByTestId('assistant-message')).toBeNull()
  })
})

const idleChat = (over = {}) => ({ messages: [], pending: null, error: null, send: vi.fn(async () => true), retry: vi.fn(), ...over })
const idleUploads = (over = {}) => ({ items: [], busy: false, add: vi.fn(() => ({ accepted: [], rejected: [] })), retry: vi.fn(),
  canRetry: () => false, checkAgain: vi.fn(), retryRefresh: vi.fn(), dismiss: vi.fn(), ...over })

function panel(props = {}) {
  return render(
    <AssistantPanel open onClose={() => {}} chat={idleChat()} uploads={idleUploads()} canUpload hasLibrary chatEnabled
      uploadBlockedReason="" chatBlockedReason="" context={null} contextLabel={() => 'Central Question'}
      onClearContext={() => {}} onReference={() => {}} focusTick={0} {...props} />,
  )
}

describe('AssistantPanel', () => {
  it('defaults to "Add to library"; evaluating must be chosen explicitly', () => {
    const uploads = idleUploads()
    panel({ uploads })
    expect(screen.getByRole('radio', { name: 'Add to library' }).getAttribute('aria-checked')).toBe('true')
    const file = new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByTestId('pdf-input'), { target: { files: [file] } })
    expect(uploads.add).toHaveBeenLastCalledWith([file], 'library')
    fireEvent.click(screen.getByRole('radio', { name: 'Evaluate a new paper' }))
    fireEvent.change(screen.getByTestId('pdf-input'), { target: { files: [file] } })
    expect(uploads.add).toHaveBeenLastCalledWith([file], 'evaluate')
  })

  it('disables evaluation until a library exists', () => {
    panel({ hasLibrary: false })
    expect(screen.getByRole('radio', { name: 'Evaluate a new paper' }).disabled).toBe(true)
  })

  it('disables uploads and chat on offline/sample data, with the reason', () => {
    panel({ canUpload: false, uploadBlockedReason: 'Uploads are disabled while showing offline or sample data.',
      chatEnabled: false, chatBlockedReason: 'The assistant needs the live server.' })
    expect(screen.getByRole('button', { name: /Choose PDFs/ }).disabled).toBe(true)
    expect(screen.getByText(/disabled while showing offline/)).toBeTruthy()
    expect(screen.getByRole('textbox').disabled).toBe(true)
    expect(screen.getByRole('textbox').getAttribute('placeholder')).toMatch(/live server/)
  })

  it('sends the draft with the attached graph item', async () => {
    const chat = idleChat()
    const ctx = { kind: 'relationship', id: 5 }
    panel({ chat, context: ctx, contextLabel: () => 'connection Node 3 ↔ Node 6' })
    expect(screen.getByText('connection Node 3 ↔ Node 6')).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Why are these connected?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(chat.send).toHaveBeenCalledWith('Why are these connected?', ctx)
  })

  it('shows honest upload states and only the actions each state allows', () => {
    const items = [
      { key: 'k1', name: 'a.pdf', mode: 'library', status: 'analyzing', startedAt: Date.now() },
      { key: 'k2', name: 'b.pdf', mode: 'evaluate', status: 'unknown', serverState: 'processing',
        message: 'The server is still analyzing this paper. Check again in a moment.' },
      { key: 'k3', name: 'c.pdf', mode: 'library', status: 'done', refreshFailed: true,
        result: { mode: 'library', claims_kept: 3, relationships_created: 2 } },
    ]
    const uploads = idleUploads({ items, canRetry: (it) => it.status === 'failed' })
    panel({ uploads })
    const rows = screen.getAllByRole('listitem')
    expect(rows[0].textContent).toMatch(/Analyzing · 0:0\d/)
    expect(rows[0].textContent).not.toMatch(/%/)
    expect(within(rows[1]).queryByRole('button', { name: 'Retry upload' })).toBeNull()
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Check again' }))
    expect(uploads.checkAgain).toHaveBeenCalledWith('k2')
    expect(rows[2].textContent).toMatch(/3 claims · 2 new relationships/)
    fireEvent.click(within(rows[2]).getByRole('button', { name: 'Refresh graph' }))
    expect(uploads.retryRefresh).toHaveBeenCalledWith('k3')
  })
})

describe('Inspector → assistant', () => {
  const graph = JSON.parse(readFileSync(resolve(process.cwd(), 'public/fixtures/graph_with_new.json'), 'utf8'))
  it('"Ask about this connection" passes the relationship as chat context', () => {
    const onAsk = vi.fn()
    render(<Inspector graph={graph} selection={{ kind: 'relationship', id: 1 }} refs={nodeRefs(graph)}
      onNavigate={() => {}} onClose={() => {}} onAsk={onAsk} />)
    const btn = screen.getByRole('button', { name: 'Ask about this connection' })
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    expect(onAsk).toHaveBeenCalledWith({ kind: 'relationship', id: 1 })
  })
})
