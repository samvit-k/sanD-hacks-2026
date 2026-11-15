import { useCallback, useRef, useState } from 'react'
import { api, ApiError, TimeoutError } from '../api/client.js'

// Drafting the central research question. Three things are kept apart:
//  - `interest`: the researcher's own words, never altered;
//  - `proposals`: Gemini suggestions (suggestions only; the list lets "Regenerate" avoid repeats);
//  - `draft`: the exact text that would be saved, always editable, labelled by its `origin`.
// Nothing is written to the server except by `confirm()`, which the UI calls only from an explicit
// "Confirm" click. Gemini never finalizes the question.
//
// Safety:
//  - A plain confirm sends no destructive flag. `keep_papers` / `reset` are sent only as the user's
//    answer to a server conflict, only for the exact question that conflict was about (editing the
//    draft clears the conflict, so a new confirmation is required), and never both at once.
//  - A suggestion that arrives after the user edited the draft is added to `proposals` but does not
//    replace the user's text; a superseded (stale) response is ignored entirely.
//  - A successful save is final: if the follow-up UI update (`onConfirmed`) fails, the hook reports a
//    recoverable "saved, but the view was not updated" error and never re-sends the question.
//
// Source-agnostic: `offerProposals(list, source)` lets paper-first onboarding (later) feed questions
// proposed from uploaded papers into the same review → edit → confirm flow.

export const MAX_QUESTION_LEN = 300
export const ORIGIN = { gemini: 'gemini', edited: 'edited', manual: 'manual' }
export const CHOICES = ['keep_papers', 'reset']

export const clean = (text) => (text ?? '').split(/\s+/).filter(Boolean).join(' ')

function proposeMessage(err) {
  if (err instanceof TimeoutError) return 'Gemini took too long to answer. Try again, or write the question yourself.'
  if (err instanceof ApiError) return err.message
  return `${err?.message ?? 'Something went wrong'}. No question was suggested or saved.`
}

const FLAGS = { keep_papers: { keepPapers: true }, reset: { reset: true } }

export function useQuestionComposer({ currentQuestion = null, onConfirmed } = {}) {
  const [mode, setModeState] = useState('interest') // 'interest' | 'manual'
  const [interest, setInterestState] = useState('')
  const [proposals, setProposals] = useState([]) // [{question, rationale, interest, source}]
  const [draft, setDraftState] = useState('')
  const [origin, setOrigin] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [keptEdits, setKeptEdits] = useState(false) // a suggestion arrived but the user's edits were kept
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState(null) // {kind: 'propose' | 'confirm' | 'after_save', message}
  const [conflict, setConflict] = useState(null) // 409 detail + the exact question it was about
  const [saved, setSaved] = useState(null) // {question, workspace} once the server confirmed the save
  const reqRef = useRef(0)
  const busyRef = useRef(false)
  const confirmRef = useRef(false)
  const draftVersion = useRef(0) // bumped on every user edit of the draft
  const latest = proposals.length ? proposals[proposals.length - 1] : null

  /** Add suggestions. The draft is replaced only when `replaceDraft` (the user has not edited it). */
  const offerProposals = useCallback((list, source = 'gemini', { replaceDraft = true } = {}) => {
    if (!list?.length) return
    setProposals((old) => [...old, ...list.map((p) => ({ ...p, source }))])
    if (replaceDraft) {
      setDraftState(list[list.length - 1].question)
      setOrigin(ORIGIN.gemini)
      setConflict(null)
      setKeptEdits(false)
    } else {
      setKeptEdits(true)
    }
  }, [])

  const generate = async ({ again = false } = {}) => {
    const text = clean(interest)
    if (!text || busyRef.current) return false
    busyRef.current = true
    const id = ++reqRef.current
    const versionAtStart = draftVersion.current
    setGenerating(true)
    setError(null)
    const previous = again ? proposals.filter((p) => p.interest === text).map((p) => p.question) : []
    try {
      const res = await api.proposeQuestion(text, previous)
      if (id !== reqRef.current) return false // superseded: never touches newer state
      offerProposals([{ ...res.proposal, interest: res.interest }], res.source ?? 'gemini',
        { replaceDraft: draftVersion.current === versionAtStart })
      return true
    } catch (err) {
      if (id === reqRef.current) setError({ kind: 'propose', message: proposeMessage(err) })
      return false
    } finally {
      if (id === reqRef.current) {
        busyRef.current = false
        setGenerating(false)
      }
    }
  }

  /** The user typed in the draft. */
  const setDraft = (text) => {
    draftVersion.current += 1
    setDraftState(text)
    setConflict(null) // a conflict decision applies only to the exact question it was shown for
    setKeptEdits(false)
    if (latest && clean(text) === latest.question) setOrigin(ORIGIN.gemini)
    else setOrigin(latest ? ORIGIN.edited : ORIGIN.manual)
  }

  /** Put a suggestion (by default the latest) into the draft. */
  const pickProposal = (p = latest) => {
    if (!p) return
    draftVersion.current += 1
    setDraftState(p.question)
    setOrigin(ORIGIN.gemini)
    setConflict(null)
    setKeptEdits(false)
  }

  /** Abandon an in-flight suggestion: its response, if it still arrives, is ignored. */
  const cancelGeneration = () => {
    if (!busyRef.current) return
    reqRef.current += 1
    busyRef.current = false
    setGenerating(false)
  }

  /** A suggestion requested for different words must not become the draft for the new words. */
  const setInterest = (text) => {
    if (clean(text) !== clean(interest)) cancelGeneration()
    setInterestState(text)
  }

  const setMode = (m) => {
    if (m === 'manual') cancelGeneration()
    setModeState(m)
    setError(null)
    if (m === 'manual' && !draft) setOrigin(ORIGIN.manual)
  }

  const question = clean(draft)
  const alreadySaved = !!saved && saved.question === question
  const unchanged = (!!currentQuestion && question === clean(currentQuestion)) || alreadySaved
  const canConfirm = !!question && question.length <= MAX_QUESTION_LEN && !unchanged && !confirming && !generating

  const runAfterSave = async (ws, q, from) => {
    try {
      await onConfirmed?.(ws, { question: q, origin: from })
      setError(null)
    } catch (err) {
      setError({ kind: 'after_save', message: `The question was saved, but the view could not be updated (${err?.message ?? 'unknown error'}).` })
    }
  }

  /**
   * Save the draft. `choice` ('keep_papers' | 'reset') is accepted only as the user's explicit answer to
   * the current conflict, and only while the draft is still the question that conflict was about.
   */
  const confirm = async (choice = null) => {
    if (!canConfirm || confirmRef.current) return false
    if (choice !== null) {
      if (!CHOICES.includes(choice) || !conflict || conflict.question !== question) {
        setConflict(null)
        return false // stale or unknown decision: the user must confirm again
      }
    }
    confirmRef.current = true
    setConfirming(true)
    setError(null)
    let ws
    try {
      ws = await api.setQuestion(question, choice ? FLAGS[choice] : {})
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.detail?.code === 'question_conflict') {
        setConflict({ ...err.detail, question })
      } else {
        setError({ kind: 'confirm', message: `The question was not saved: ${err?.message ?? 'unknown error'}` })
      }
      return false
    } finally {
      confirmRef.current = false
      setConfirming(false)
    }
    // Saved on the server. From here on nothing re-sends it.
    setConflict(null)
    setSaved({ question, workspace: ws })
    await runAfterSave(ws, question, origin)
    return true
  }

  const retryAfterSave = () => saved && runAfterSave(saved.workspace, saved.question, origin)

  return {
    mode, setMode, interest, setInterest, proposals, latest, draft, setDraft, origin, pickProposal,
    revert: () => pickProposal(latest), keptEdits,
    generating, generate, regenerate: () => generate({ again: true }), cancelGeneration, offerProposals,
    confirming, confirm, canConfirm, unchanged, saved, retryAfterSave,
    error, conflict, cancelConflict: () => setConflict(null),
  }
}
