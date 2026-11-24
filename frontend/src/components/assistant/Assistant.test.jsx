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
  it('defaults to "Add to Library"; evaluating must be chosen explicitly', () => {
    const uploads = idleUploads()
    panel({ uploads })
    expect(screen.getByRole('radio', { name: 'Add to Library' }).getAttribute('aria-checked')).toBe('true')
    const file = new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByTestId('pdf-input'), { target: { files: [file] } })
    expect(uploads.add).toHaveBeenLastCalledWith([file], 'library')
    fireEvent.click(screen.getByRole('radio', { name: 'Evaluate a New Paper' }))
    fireEvent.change(screen.getByTestId('pdf-input'), { target: { files: [file] } })
    expect(uploads.add).toHaveBeenLastCalledWith([file], 'evaluate')
  })

  it('disables evaluation until a library exists', () => {
    panel({ hasLibrary: false })
    expect(screen.getByRole('radio', { name: 'Evaluate a New Paper' }).disabled).toBe(true)
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

describe('minimizing chat exchanges', () => {
  const Q1 = { id: 1, role: 'user', content: 'Does Paper D disprove Paper A?', context: null }
  const A1 = { ...ANSWER, id: 2, content: 'First answer text.' }
  const Q2 = { id: 3, role: 'user', content: 'What about long-term retention?', context: null }
  const A2 = { ...ANSWER, id: 4, content: 'Second answer text.' }
  const uploads = { items: [], busy: false, add: vi.fn(() => ({ accepted: [], rejected: [] })), retry: vi.fn(),
    canRetry: () => false, checkAgain: vi.fn(), retryRefresh: vi.fn(), dismiss: vi.fn() }
  const show = () => render(
    <AssistantPanel open onClose={() => {}} uploads={uploads} canUpload hasLibrary chatEnabled uploadBlockedReason=""
      chatBlockedReason="" context={null} contextLabel={() => ''} onClearContext={() => {}} onReference={() => {}}
      focusTick={0} chat={{ messages: [Q1, A1, Q2, A2], pending: null, error: null, send: vi.fn(), retry: vi.fn() }} />)

  it('each question can be minimized, hiding only its own answer, and expanded again', () => {
    show()
    const [first] = screen.getAllByRole('button', { name: 'Minimize' })
    fireEvent.click(first)
    expect(screen.queryByText('First answer text.')).toBeNull()
    expect(screen.getByText('Second answer text.')).toBeTruthy()          // the other exchange is untouched
    expect(screen.getByText('Does Paper D disprove Paper A?')).toBeTruthy() // the question stays (one line)
    expect(screen.getByText('Answer minimized')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }))
    expect(screen.getByText('First answer text.')).toBeTruthy()
  })

  it('Minimize All collapses every exchange and Expand All restores them', () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Minimize All' }))
    expect(screen.queryByText('First answer text.')).toBeNull()
    expect(screen.queryByText('Second answer text.')).toBeNull()
    expect(screen.getAllByText('Answer minimized')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Expand All' }))
    expect(screen.getByText('First answer text.')).toBeTruthy()
    expect(screen.getByText('Second answer text.')).toBeTruthy()
  })
})

describe('clearing the chat', () => {
  const convo = [{ id: 1, role: 'user', content: 'Do tutors help?', cited: [], references: {} },
    { ...ANSWER, id: 2 }]

  it('asks for confirmation, then clears through the chat hook and reports it', async () => {
    const clear = vi.fn(async () => ({ deleted: 2 }))
    const onChatCleared = vi.fn()
    panel({ chat: idleChat({ messages: convo, clear }), projectTitle: 'AI tutors', onChatCleared })
    fireEvent.click(screen.getByRole('button', { name: 'Clear Chat' }))
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toContain('2 messages')
    expect(dialog.textContent).toContain('AI tutors')
    expect(dialog.textContent).toMatch(/Papers, claims and the evidence graph are not affected/)
    expect(clear).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Clear Chat' }))
    await vi.waitFor(() => expect(onChatCleared).toHaveBeenCalledWith({ deleted: 2 }))
    expect(clear).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('cancel changes nothing; the button is hidden without messages and disabled while answering', () => {
    const clear = vi.fn()
    const { unmount } = panel({ chat: idleChat({ messages: convo, clear }) })
    fireEvent.click(screen.getByRole('button', { name: 'Clear Chat' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(clear).not.toHaveBeenCalled()
    unmount()
    const { unmount: u2 } = panel({ chat: idleChat({ messages: [] }) })
    expect(screen.queryByRole('button', { name: 'Clear Chat' })).toBeNull()
    u2()
    panel({ chat: idleChat({ messages: convo, pending: { text: 'x', token: 1 } }) })
    expect(screen.getByRole('button', { name: 'Clear Chat' }).disabled).toBe(true)
  })

  it('a failed clear keeps the dialog open with the server message', async () => {
    const clear = vi.fn(async () => { throw new Error('Cannot reach the SpiderMonkey server') })
    panel({ chat: idleChat({ messages: convo, clear }) })
    fireEvent.click(screen.getByRole('button', { name: 'Clear Chat' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Clear Chat' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain('Cannot reach')
  })
})

describe('chat turns whose evidence was removed', () => {
  it('shows an "Evidence removed" notice naming the paper, and no stale quotes', () => {
    render(<ChatMessage message={{ ...ANSWER, cited: [], evidence_removed: true,
      removed_evidence: [{ paper_id: 'paper-a', title: 'AI tutor RCT' }] }} />)
    const note = screen.getByTestId('removed-evidence')
    expect(note.textContent).toContain('“AI tutor RCT”')
    expect(note.textContent).toMatch(/not used as context/)
    expect(document.querySelector('.vq')).toBeNull()
    expect(screen.getByText(/source sentences were removed with the paper/)).toBeTruthy()
  })

  it('also marks withheld answers, and shows nothing for intact turns', () => {
    const { unmount } = render(<ChatMessage message={{ ...ANSWER, guardrail: 'uncited_findings', content: 'Withheld.',
      cited: [], evidence_removed: true, removed_evidence: [{ paper_id: 'p', title: 'P' }] }} />)
    expect(screen.getByTestId('removed-evidence')).toBeTruthy()
    unmount()
    render(<ChatMessage message={ANSWER} />)
    expect(screen.queryByTestId('removed-evidence')).toBeNull()
  })
})
