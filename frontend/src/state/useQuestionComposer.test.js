import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, sessionApi } from '../api/client.js'

const client = sessionApi('aaaaaaaaaaaa') // the session these tests run in
import { ORIGIN, useQuestionComposer } from './useQuestionComposer.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const INTEREST = 'how AI tutoring affects student learning outcomes'
const Q1 = 'How does AI-assisted tutoring influence student learning outcomes compared with traditional instruction?'
const Q2 = 'Is AI tutoring associated with different student learning outcomes than human tutoring?'
const proposal = (question, interest = INTEREST) => ({ interest, proposal: { question, rationale: 'r' }, source: 'gemini', saved: false })
const CONFLICT = { code: 'question_conflict', current_question: 'Old?', counts: { papers: 5, claims: 15 }, options: ['keep_papers', 'reset'] }

let proposeCalls
let saveCalls

beforeEach(() => {
  proposeCalls = []
  saveCalls = []
  vi.spyOn(client, 'proposeQuestion').mockImplementation((interest, previous) => {
    const d = deferred()
    proposeCalls.push({ interest, previous, ...d })
    return d.promise
  })
  vi.spyOn(client, 'setQuestion').mockImplementation((question, flags) => {
    const d = deferred()
    saveCalls.push({ question, flags, ...d })
    return d.promise
  })
})
afterEach(() => vi.restoreAllMocks())

function setup(opts = {}) {
  const onConfirmed = opts.onConfirmed ?? vi.fn()
  const hook = renderHook((props) => useQuestionComposer(props), { initialProps: { client, currentQuestion: null, onConfirmed, ...opts } })
  return { ...hook, onConfirmed }
}

function start(result, interest = INTEREST) {
  act(() => result.current.setInterest(interest))
  let p
  act(() => { p = result.current.generate() })
  return p
}

async function suggest(result, question = Q1) {
  const p = start(result)
  await act(async () => { proposeCalls.at(-1).resolve(proposal(question)); await p })
}

async function confirmWithConflict(result) {
  let p
  act(() => { p = result.current.confirm() })
  await act(async () => { saveCalls.at(-1).reject(new ApiError(409, CONFLICT)); await p })
}

describe('generation and review', () => {
  it('a suggestion fills the draft but never saves it', async () => {
    const { result } = setup()
    await suggest(result)
    expect(result.current.draft).toBe(Q1)
    expect(result.current.origin).toBe(ORIGIN.gemini)
    expect(result.current.interest).toBe(INTEREST) // the user's own words stay as typed
    expect(client.setQuestion).not.toHaveBeenCalled()
  })

  it('regenerate sends earlier suggestions for the same interest', async () => {
    const { result } = setup()
    await suggest(result)
    let p
    act(() => { p = result.current.regenerate() })
    expect(proposeCalls.at(-1).previous).toEqual([Q1])
    await act(async () => { proposeCalls.at(-1).resolve(proposal(Q2)); await p })
    expect(result.current.draft).toBe(Q2)
    expect(result.current.proposals.map((x) => x.question)).toEqual([Q1, Q2])
  })

  it('reports a Gemini failure without touching the draft or the workspace', async () => {
    const { result } = setup()
    act(() => result.current.setDraft('My own question?'))
    const p = start(result)
    await act(async () => { proposeCalls.at(-1).reject(new ApiError(503, { message: 'Gemini was unavailable.' })); await p })
    expect(result.current.error).toEqual({ kind: 'propose', message: 'Gemini was unavailable.' })
    expect(result.current.draft).toBe('My own question?')
    expect(result.current.generating).toBe(false)
    expect(client.setQuestion).not.toHaveBeenCalled()
  })

  it('editing a suggestion marks it as edited; manual entry needs no Gemini', async () => {
    const { result } = setup()
    await suggest(result)
    act(() => result.current.setDraft(Q1.replace('traditional instruction', 'human tutoring')))
    expect(result.current.origin).toBe(ORIGIN.edited)
    const manual = setup()
    act(() => manual.result.current.setMode('manual'))
    act(() => manual.result.current.setDraft('Do spaced repetition apps improve vocabulary?'))
    expect(manual.result.current.origin).toBe(ORIGIN.manual)
    expect(client.proposeQuestion).toHaveBeenCalledTimes(1)
  })

  it('a late suggestion never overwrites a draft the user edited meanwhile', async () => {
    const { result } = setup()
    const p = start(result)
    act(() => result.current.setDraft('My edited question about tutoring?'))
    await act(async () => { proposeCalls.at(-1).resolve(proposal(Q1)); await p })
    expect(result.current.draft).toBe('My edited question about tutoring?')
    expect(result.current.keptEdits).toBe(true)
    expect(result.current.latest.question).toBe(Q1) // kept and selectable afterwards
  })

  it('pickProposal fills the draft as Gemini text, clears a stale conflict, and saves nothing', async () => {
    const { result } = setup({ currentQuestion: 'Old?' })
    await suggest(result)
    act(() => result.current.setDraft('Something else entirely?'))
    await confirmWithConflict(result)
    expect(result.current.conflict).not.toBeNull()
    act(() => result.current.pickProposal())
    expect(result.current.draft).toBe(Q1)
    expect(result.current.origin).toBe(ORIGIN.gemini)
    expect(result.current.conflict).toBeNull()
    expect(saveCalls).toHaveLength(1) // only the explicit confirm above
  })
})

describe('cancellation and stale responses', () => {
  it('a cancelled request cannot update the draft, the proposals or the error', async () => {
    const { result } = setup()
    act(() => result.current.setDraft('Keep me?'))
    const p = start(result)
    act(() => result.current.cancelGeneration())
    expect(result.current.generating).toBe(false)
    await act(async () => { proposeCalls[0].resolve(proposal(Q1)); await p })
    expect(result.current.draft).toBe('Keep me?')
    expect(result.current.proposals).toEqual([])
    let p2
    act(() => { p2 = result.current.generate() })
    act(() => result.current.cancelGeneration())
    await act(async () => { proposeCalls[1].reject(new ApiError(503, 'down')); await p2 })
    expect(result.current.error).toBeNull()
  })

  it('a new generation can start right after cancelling, and the stale reply cannot overwrite it', async () => {
    const { result } = setup()
    const first = start(result)
    act(() => result.current.cancelGeneration())
    let second
    act(() => { second = result.current.generate() })
    expect(proposeCalls).toHaveLength(2)
    await act(async () => { proposeCalls[1].resolve(proposal(Q2)); await second })
    await act(async () => { proposeCalls[0].resolve(proposal(Q1)); await first })
    expect(result.current.draft).toBe(Q2)
    expect(result.current.proposals.map((x) => x.question)).toEqual([Q2])
  })

  it('changing the research interest invalidates the generation for the earlier words', async () => {
    const { result } = setup()
    const p = start(result)
    act(() => result.current.setInterest('spaced repetition and vocabulary retention'))
    expect(result.current.generating).toBe(false)
    await act(async () => { proposeCalls[0].resolve(proposal(Q1)); await p })
    expect(result.current.draft).toBe('')
    expect(result.current.proposals).toEqual([])
    const p2 = start(result, 'spaced repetition and vocabulary retention')
    await act(async () => { proposeCalls[1].resolve(proposal(Q2, 'spaced repetition and vocabulary retention')); await p2 })
    expect(result.current.draft).toBe(Q2) // normal generation still works afterwards
  })
})

describe('confirmation', () => {
  it('a normal confirmation sends no destructive flags', async () => {
    const { result, onConfirmed } = setup()
    await suggest(result)
    let p
    act(() => { p = result.current.confirm() })
    expect(saveCalls[0]).toMatchObject({ question: Q1, flags: {} })
    await act(async () => { saveCalls[0].resolve({ question: Q1 }); await p })
    expect(onConfirmed).toHaveBeenCalledWith({ question: Q1 }, { question: Q1, origin: ORIGIN.gemini })
  })

  it('keep_papers sends only keepPapers, and an unresolved conflict changes nothing', async () => {
    const { result, onConfirmed } = setup({ currentQuestion: 'Old?' })
    await suggest(result)
    await confirmWithConflict(result)
    expect(result.current.conflict).toMatchObject({ question: Q1, counts: { papers: 5 } })
    expect(onConfirmed).not.toHaveBeenCalled()
    expect(result.current.saved).toBeNull()
    let p
    act(() => { p = result.current.confirm('keep_papers') })
    expect(saveCalls[1].flags).toEqual({ keepPapers: true })
    await act(async () => { saveCalls[1].resolve({ question: Q1 }); await p })
    expect(onConfirmed).toHaveBeenCalledTimes(1)
  })

  it('reset sends only reset, and only as an explicit answer to the conflict', async () => {
    const { result } = setup({ currentQuestion: 'Old?' })
    await suggest(result)
    let p
    act(() => { p = result.current.confirm('reset') }) // no conflict shown yet -> refused
    await act(async () => { await p })
    expect(saveCalls).toHaveLength(0)
    await confirmWithConflict(result)
    act(() => { p = result.current.confirm('reset') })
    expect(saveCalls[1].flags).toEqual({ reset: true })
    await act(async () => { saveCalls[1].resolve({ question: Q1 }); await p })
    expect(saveCalls.some((c) => c.flags.reset && c.flags.keepPapers)).toBe(false)
  })

  it('changing the draft invalidates an earlier conflict decision', async () => {
    const { result } = setup({ currentQuestion: 'Old?' })
    await suggest(result)
    await confirmWithConflict(result)
    act(() => result.current.setDraft(Q2))
    expect(result.current.conflict).toBeNull()
    let p
    act(() => { p = result.current.confirm('reset') })
    await act(async () => { await p })
    expect(saveCalls).toHaveLength(1) // nothing destructive sent for text the conflict was not about
  })

  it('a successful save stays successful when the UI update throws, and is not re-sent', async () => {
    const onConfirmed = vi.fn().mockRejectedValueOnce(new Error('refresh failed')).mockResolvedValueOnce(undefined)
    const { result } = setup({ onConfirmed })
    await suggest(result)
    let p
    let ok
    act(() => { p = result.current.confirm() })
    await act(async () => { saveCalls[0].resolve({ question: Q1 }); ok = await p })
    expect(ok).toBe(true)
    expect(result.current.saved.question).toBe(Q1)
    expect(result.current.error.kind).toBe('after_save')
    expect(result.current.error.message).toMatch(/was saved/)
    expect(result.current.error.message).not.toMatch(/not saved/)
    expect(result.current.canConfirm).toBe(false) // no accidental resubmission
    await act(async () => { await result.current.retryAfterSave() })
    expect(onConfirmed).toHaveBeenCalledTimes(2)
    expect(result.current.error).toBeNull()
    expect(saveCalls).toHaveLength(1)
  })

  it('a failed save never calls onConfirmed', async () => {
    const { result, onConfirmed } = setup()
    await suggest(result)
    let p
    act(() => { p = result.current.confirm() })
    await act(async () => { saveCalls[0].reject(new ApiError(503, 'Database unavailable')); await p })
    expect(onConfirmed).not.toHaveBeenCalled()
    expect(result.current.error).toEqual({ kind: 'confirm', message: 'The question was not saved: Database unavailable' })
    expect(result.current.saved).toBeNull()
  })

  it('repeated confirm clicks cannot start overlapping saves', async () => {
    const { result } = setup()
    await suggest(result)
    let p
    act(() => { p = result.current.confirm(); result.current.confirm(); result.current.confirm() })
    expect(saveCalls).toHaveLength(1)
    await act(async () => { saveCalls[0].resolve({ question: Q1 }); await p })
    act(() => { result.current.confirm() })
    expect(saveCalls).toHaveLength(1)
  })

  it('the current question cannot be "confirmed" again', async () => {
    const { result } = setup({ currentQuestion: Q1 })
    await suggest(result)
    expect(result.current.unchanged).toBe(true)
    expect(result.current.canConfirm).toBe(false)
  })
})
describe('paper-first suggestions', () => {
  it('suggests from picked PDFs and hands the files to onConfirmed', async () => {
    const files = [new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })]
    const client = {
      proposeQuestionFromPapers: vi.fn(async () => ({ proposal: { question: 'Do AI tutors affect learning?', rationale: null }, papers: ['A'], source: 'gemini_papers' })),
      setQuestion: vi.fn(async (q) => ({ question: q })),
    }
    const onConfirmed = vi.fn()
    const { result } = renderHook(() => useQuestionComposer({ client, onConfirmed }))
    act(() => { result.current.setMode('papers'); result.current.setPaperFiles(files) })
    await act(async () => { await result.current.generate() })
    expect(client.proposeQuestionFromPapers).toHaveBeenCalledWith(files, [])
    expect(result.current.draft).toBe('Do AI tutors affect learning?')
    await act(async () => { await result.current.confirm() })
    expect(onConfirmed).toHaveBeenCalledWith({ question: 'Do AI tutors affect learning?' },
      expect.objectContaining({ files }))
  })
})
