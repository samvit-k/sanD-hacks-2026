import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

// Conversation state for one research session.
//
// Async safety:
//  - `gen` (generation) increments on every session switch. Every request captures it, and a response
//    is applied only if the generation is unchanged — so A -> B -> A never accepts a reply that was
//    started during the first visit to A.
//  - A synchronous ref guards `send`, so rapid clicks never start overlapping requests; each send has
//    a token, and only the matching response may clear `pending`.
//  - History loads are versioned; only the newest load in the current generation is applied, and it
//    is MERGED with messages already received (by server id), so a slow history response can never
//    drop a newer answer or duplicate one.
//  - Status notes (e.g. "upload finished") are kept per session id and only shown in that session.
// Messages come only from the server. The user's pending text is kept separately (`pending`) and is
// never inserted into `messages` until the server confirms it.

let noteSeq = 0

const isServerId = (id) => typeof id === 'number'

/** Union of two server message lists by id, in server (chronological) order. */
export function mergeById(a, b) {
  const byId = new Map()
  for (const m of [...a, ...b]) if (isServerId(m.id)) byId.set(m.id, m)
  return [...byId.values()].sort((x, y) => x.id - y.id)
}

/** Server messages with this session's local status notes placed after the message they followed. */
export function withNotes(messages, notes = []) {
  const rows = messages.map((m) => ({ m, key: [m.id, 0, 0] }))
  for (const n of notes) rows.push({ m: n, key: [n.afterId ?? 0, 1, n.seq] })
  rows.sort((x, y) => x.key[0] - y.key[0] || x.key[1] - y.key[1] || x.key[2] - y.key[2])
  return rows.map((r) => r.m)
}

/** `client`: the session-bound API client of the research session this conversation belongs to. */
export function useChat(client, { enabled = true } = {}) {
  const sessionId = client?.sessionId ?? null
  const [state, setState] = useState({ messages: [], loaded: false, pending: null, error: null })
  const [notes, setNotes] = useState({}) // { [sessionId]: note[] } — survives session switches
  const gen = useRef(0)
  const historyVersion = useRef(0)
  const sending = useRef(false)
  const sendToken = useRef(0)
  const lastServerId = useRef(0)

  const load = useCallback(async () => {
    const g = gen.current
    const v = ++historyVersion.current
    const sid = sessionId
    try {
      const res = await client.chatHistory()
      if (gen.current !== g || historyVersion.current !== v) return // superseded
      setState((s) => {
        const messages = mergeById(res.messages ?? [], s.messages)
        lastServerId.current = messages.length ? messages[messages.length - 1].id : 0
        return { ...s, messages, loaded: true }
      })
    } catch (error) {
      if (gen.current !== g || historyVersion.current !== v) return
      setState((s) => ({ ...s, loaded: true, error: { message: error.message, kind: 'history' } }))
    }
  }, [sessionId])

  // New session (or re-enabled): start a new generation; anything in flight is now stale.
  useEffect(() => {
    gen.current += 1
    sending.current = false
    lastServerId.current = 0
    setState({ messages: [], loaded: false, pending: null, error: null })
    if (enabled) load()
  }, [enabled, load])

  const send = useCallback(async (text, context = null) => {
    const message = (text ?? '').trim()
    if (!message || !enabled || sending.current) return false
    sending.current = true // synchronous guard: set before any await
    const g = gen.current
    const token = ++sendToken.current
    const sid = sessionId
    setState((s) => ({ ...s, pending: { text: message, context, token }, error: null }))
    try {
      const res = await client.chat({ message, context })
      if (gen.current !== g) return false // session changed meanwhile: never touch the new session
      setState((s) => {
        const messages = mergeById(s.messages, [res.user, res.assistant])
        lastServerId.current = messages.length ? messages[messages.length - 1].id : 0
        return { ...s, messages, pending: s.pending?.token === token ? null : s.pending }
      })
      return true
    } catch (error) {
      if (gen.current !== g) return false
      // The server stores nothing when it fails, so the same question can simply be retried.
      setState((s) => ({
        ...s,
        pending: s.pending?.token === token ? null : s.pending,
        error: { message: error.message, kind: 'send', text: message, context },
      }))
      return false
    } finally {
      if (gen.current === g && sendToken.current === token) sending.current = false
    }
  }, [enabled, sessionId])

  const retry = useCallback(() => {
    const err = state.error
    if (err?.kind === 'send') return send(err.text, err.context)
    if (err?.kind === 'history') {
      setState((s) => ({ ...s, error: null }))
      return load()
    }
    return undefined
  }, [state.error, send, load])

  /**
   * Local status line (e.g. an upload finished) for the session that STARTED the work.
   * Marked role "status": never presented as model output. Shown only in `forSession`.
   */
  const addNote = useCallback((content, { tone = 'info', forSession } = {}) => {
    const sid = forSession ?? sessionId
    noteSeq += 1
    const note = { id: `note-${noteSeq}`, role: 'status', tone, content, seq: noteSeq,
      afterId: sid === sessionId ? lastServerId.current : Number.MAX_SAFE_INTEGER }
    setNotes((all) => ({ ...all, [sid]: [...(all[sid] ?? []), note] }))
  }, [sessionId])

  const dismissError = useCallback(() => setState((s) => ({ ...s, error: null })), [])

  const messages = useMemo(() => withNotes(state.messages, notes[sessionId]), [state.messages, notes, sessionId])

  return {
    messages, loaded: state.loaded, pending: state.pending, error: state.error, sessionId,
    send, retry, addNote, dismissError, reload: load,
  }
}
