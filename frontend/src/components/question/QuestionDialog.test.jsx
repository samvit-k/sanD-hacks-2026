import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, sessionApi } from '../../api/client.js'

const client = sessionApi('aaaaaaaaaaaa') // the session these tests run in
import AssistantPanel, { withQuestionDividers } from '../assistant/AssistantPanel.jsx'
import WorkspacePanel from '../workspace/WorkspacePanel.jsx'
import QuestionDialog from './QuestionDialog.jsx'

const INTEREST = "I'm interested in how AI tutoring affects student learning outcomes."
const Q1 = 'How does AI-assisted tutoring influence student learning outcomes compared with traditional instruction?'
const Q2 = 'Is AI tutoring associated with different student learning outcomes than human tutoring?'
const OLD = 'Do AI tutors improve learning?'
const CONFLICT = {
  code: 'question_conflict', current_question: OLD, options: ['keep_papers', 'reset'],
  counts: { papers: 5, claims: 15, relationships: 24, impacts: 6 },
}

const reply = (question) => Promise.resolve({ interest: INTEREST, proposal: { question, rationale: 'Names the comparison.' }, source: 'gemini', saved: false })

beforeEach(() => {
  vi.spyOn(client, 'proposeQuestion').mockImplementation(() => reply(Q1))
  vi.spyOn(client, 'setQuestion').mockImplementation((question) => Promise.resolve({ question, pending_papers: [] }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function open(workspace = { question: null, counts: {} }, onConfirmed = vi.fn()) {
  render(<QuestionDialog open client={client} workspace={workspace} onClose={() => {}} onConfirmed={onConfirmed} />)
  return onConfirmed
}

async function suggest() {
  fireEvent.change(screen.getByLabelText(/Your Research Interest/), { target: { value: INTEREST } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Suggest a Question' })) })
}

const draftBox = () => screen.getByLabelText('Central Research Question')

describe('QuestionDialog', () => {
  it('shows the user\'s words and Gemini\'s suggestion separately, and saves only on Confirm', async () => {
    const onConfirmed = open()
    await suggest()
    expect(screen.getByLabelText(/Your Research Interest/).value).toBe(INTEREST)
    expect(draftBox().value).toBe(Q1)
    expect(screen.getByTestId('question-origin').textContent).toMatch(/Suggested by Gemini — review/)
    expect(screen.getByText(/Names the comparison/)).toBeTruthy()
    expect(client.setQuestion).not.toHaveBeenCalled() // a suggestion is never saved by itself
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm Question' })) })
    expect(client.setQuestion).toHaveBeenCalledWith(Q1, {})
    expect(onConfirmed).toHaveBeenCalledTimes(1)
  })

  it('edits are labelled and confirmed exactly as typed', async () => {
    open()
    await suggest()
    const edited = Q1.replace('traditional instruction', 'human tutoring')
    fireEvent.change(draftBox(), { target: { value: edited } })
    expect(screen.getByTestId('question-origin').textContent).toMatch(/edited by you/)
    fireEvent.click(screen.getByRole('button', { name: "Revert to Gemini's Suggestion" }))
    expect(draftBox().value).toBe(Q1)
    fireEvent.change(draftBox(), { target: { value: edited } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm Question' })) })
    expect(client.setQuestion).toHaveBeenCalledWith(edited, {})
  })

  it('regenerates a different suggestion', async () => {
    open()
    await suggest()
    client.proposeQuestion.mockImplementation(() => reply(Q2))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Regenerate Suggestion' })) })
    expect(client.proposeQuestion).toHaveBeenLastCalledWith(INTEREST, [Q1])
    expect(draftBox().value).toBe(Q2)
  })

  it('a question can be written directly without Gemini', async () => {
    open()
    fireEvent.click(screen.getByRole('radio', { name: 'Write It Myself' }))
    fireEvent.change(draftBox(), { target: { value: 'Do spaced repetition apps improve vocabulary retention?' } })
    expect(screen.getByTestId('question-origin').textContent).toBe('Written by you')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm Question' })) })
    expect(client.proposeQuestion).not.toHaveBeenCalled()
    expect(client.setQuestion).toHaveBeenCalledWith('Do spaced repetition apps improve vocabulary retention?', {})
  })

  it('a Gemini failure is shown and offers the manual route; nothing is saved', async () => {
    client.proposeQuestion.mockImplementation(() => Promise.reject(new ApiError(503, {
      message: 'Gemini was unavailable (RuntimeError). No question was suggested or saved; try again, or write the question yourself.' })))
    open()
    await suggest()
    expect(screen.getByRole('alert').textContent).toMatch(/write the question yourself/)
    expect(client.setQuestion).not.toHaveBeenCalled()
    expect(screen.getByRole('radio', { name: 'Write It Myself' })).toBeTruthy()
  })

  it('an existing project shows the consequences and changes nothing until a choice is made', async () => {
    client.setQuestion.mockImplementationOnce(() => Promise.reject(new ApiError(409, CONFLICT)))
    const onConfirmed = open({ question: OLD, counts: CONFLICT.counts })
    await suggest()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm Question' })) })
    const panel = screen.getByRole('alertdialog')
    expect(panel.textContent).toMatch(/not.*reinterpreted/)
    expect(panel.textContent).toMatch(/Kept:.*5 papers/)
    expect(panel.textContent).toMatch(/Cleared:.*15 claims, 24 relationships, 6 impacts/)
    expect(onConfirmed).not.toHaveBeenCalled()
    fireEvent.click(within(panel).getByRole('button', { name: 'Keep Current Question' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(client.setQuestion).toHaveBeenCalledTimes(1)
  })

  it('"keep papers" is one explicit click; deleting papers needs a second, explicit confirmation', async () => {
    client.setQuestion.mockImplementation((question, flags) => (flags.keepPapers || flags.reset
      ? Promise.resolve({ question }) : Promise.reject(new ApiError(409, CONFLICT))))
    open({ question: OLD, counts: CONFLICT.counts })
    await suggest()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm Question' })) })
    fireEvent.click(screen.getByRole('button', { name: 'Start Over Without These Papers…' }))
    expect(screen.getByText(/permanently deletes 5 papers/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Clear Analysis and Use New Question' })) })
    expect(client.setQuestion).toHaveBeenLastCalledWith(Q1, { keepPapers: true })
    expect(client.setQuestion.mock.calls.some(([, f]) => f.reset)).toBe(false)
  })
})

describe('workspace integration', () => {
  const ws = (over = {}) => ({ question: OLD, counts: { library_papers: 4, claims: 0 }, pending_papers: [], ...over })
  const panel = (props) => render(<WorkspacePanel graph={{ papers: {}, nodes: [], edges: [] }} refs={{}} selection={null}
    onSelectPaper={() => {}} onSelectQuestion={() => {}} collapsed={false} onToggle={() => {}} {...props} />)

  it('offers to set or change the question', () => {
    const onEdit = vi.fn()
    panel({ workspace: ws({ question: null }), onEditQuestion: onEdit, canEditQuestion: true })
    fireEvent.click(screen.getByRole('button', { name: 'Set the central question' }))
    expect(onEdit).toHaveBeenCalled()
    cleanup()
    panel({ workspace: ws(), onEditQuestion: onEdit, canEditQuestion: false })
    expect(screen.getByRole('button', { name: 'Change Question' }).disabled).toBe(true)
  })

  it('re-analysis starts only when the user clicks it, with the pending papers', () => {
    const pending = [{ id: 'a', title: 'A', is_new: false }, { id: 'd', title: 'D', is_new: true }]
    const reanalysis = { running: false, done: 0, total: 0, current: null, error: null, start: vi.fn() }
    panel({ workspace: ws({ pending_papers: pending }), onEditQuestion: () => {}, canEditQuestion: true, reanalysis })
    expect(screen.getByTestId('reanalysis').textContent).toMatch(/2 papers await analysis under the new question/)
    expect(reanalysis.start).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Re-analyze 2 papers' }))
    expect(reanalysis.start).toHaveBeenCalledWith(pending)
  })
})

describe('chat compatibility', () => {
  const m = (id, role, question) => ({ id, role, content: `${role} ${id}`, question, cited: [], references: {} })

  it('marks turns asked under an earlier question with a divider', () => {
    const rows = withQuestionDividers([m(1, 'user', OLD), m(2, 'assistant', OLD), m(3, 'user', Q1), m(4, 'assistant', Q1)], Q1)
    expect(rows.filter((r) => r.divider).map((r) => r.text)).toEqual(['Asked under an earlier central question', 'Current central question'])
    expect(rows.filter((r) => r.message).map((r) => r.stale)).toEqual([true, true, false, false])
    expect(withQuestionDividers([m(1, 'user', Q1)], Q1).some((r) => r.divider)).toBe(false) // no noise without a change
  })

  it('renders earlier-question turns dimmed in the assistant panel', () => {
    const chat = { messages: [m(1, 'user', OLD), m(3, 'user', Q1)], pending: null, error: null, send: vi.fn(), retry: vi.fn() }
    const uploads = { items: [], busy: false, add: vi.fn(() => ({ accepted: [], rejected: [] })), retry: vi.fn(),
      canRetry: () => false, checkAgain: vi.fn(), retryRefresh: vi.fn(), dismiss: vi.fn() }
    render(<AssistantPanel open onClose={() => {}} chat={chat} uploads={uploads} canUpload hasLibrary chatEnabled
      uploadBlockedReason="" chatBlockedReason="" context={null} contextLabel={() => ''} onClearContext={() => {}}
      onReference={() => {}} focusTick={0} currentQuestion={Q1} />)
    const [earlier, current] = screen.getAllByRole('separator')
    expect(earlier.textContent).toMatch(/earlier central question/)
    expect(earlier.textContent).toContain(OLD)
    expect(current.textContent).toContain(Q1)
    expect(screen.getByText('user 1').closest('.chat-row').className).toContain('stale')
    expect(screen.getByText('user 3').closest('.chat-row').className).not.toContain('stale')
  })
})
describe('QuestionDialog: From My Papers', () => {
  it('lists picked PDFs, lets one be removed, and suggests from the rest', async () => {
    const spy = vi.spyOn(client, 'proposeQuestionFromPapers').mockImplementation(() =>
      Promise.resolve({ proposal: { question: Q1, rationale: null }, papers: ['Paper A'], source: 'gemini_papers', saved: false }))
    open()
    fireEvent.click(screen.getByRole('radio', { name: 'From My Papers' }))
    const suggestBtn = screen.getByRole('button', { name: 'Suggest a Question' })
    expect(suggestBtn.disabled).toBe(true) // nothing picked and no stored papers
    const a = new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })
    const b = new File(['%PDF'], 'b.pdf', { type: 'application/pdf' })
    const notPdf = new File(['x'], 'notes.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByTestId('qd-pdf-input'), { target: { files: [a, b, notPdf] } })
    expect(screen.getByText('a.pdf')).toBeTruthy()
    expect(screen.queryByText('notes.txt')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Remove b.pdf' }))
    expect(screen.queryByText('b.pdf')).toBeNull()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Suggest a Question' })) })
    expect(spy).toHaveBeenCalledWith([a], [])
    expect(draftBox().value).toBe(Q1)
    expect(screen.getByText(/Based on: Paper A/)).toBeTruthy()
  })

  it('offers the project\'s stored papers when none are picked', () => {
    open({ question: OLD, counts: { papers: 3 } })
    fireEvent.click(screen.getByRole('radio', { name: 'From My Papers' }))
    expect(screen.getByText(/use the 3 papers in this project/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Suggest a Question' }).disabled).toBe(false)
  })
})
