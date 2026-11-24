import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChat } from './useChat.js'

// Deferred promises let each test decide exactly when (and in which order) responses arrive.
function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const msg = (id, role, content, session = 'default') => ({ id, role, content, session_id: session, cited: [], references: {} })
const reply = (n, session = 'default') => ({ user: msg(n, 'user', `q${n}`, session), assistant: msg(n + 1, 'assistant', `a${n}`, session) })

let historyCalls
let chatCalls
let clients

// One stable fake client per session id (like sessionApi(id)); every call records the session it was made in.
function clientFor(sid) {
  if (!clients.has(sid)) {
    clients.set(sid, {
      sessionId: sid,
      chatHistory: vi.fn(() => {
        const d = deferred()
        historyCalls.push({ sid, ...d })
        return d.promise
      }),
      chat: vi.fn((body) => {
        const d = deferred()
        chatCalls.push({ body: { ...body, sessionId: sid }, ...d })
        return d.promise
      }),
    })
  }
  return clients.get(sid)
}

beforeEach(() => {
  historyCalls = []
  chatCalls = []
  clients = new Map()
})

afterEach(() => vi.restoreAllMocks())

async function ready(result) {
  await act(async () => { historyCalls.at(-1).resolve({ messages: [] }) })
  await waitFor(() => expect(result.current.loaded).toBe(true))
}

describe('useChat', () => {
  it('sends a message and shows the confirmed pair exactly once', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    await ready(result)
    let p
    act(() => { p = result.current.send('Do tutors help?') })
    expect(result.current.pending.text).toBe('Do tutors help?')
    expect(result.current.messages).toHaveLength(0) // pending text is NOT a server message
    await act(async () => { chatCalls[0].resolve(reply(1)); await p })
    expect(result.current.pending).toBeNull()
    expect(result.current.messages.map((m) => m.id)).toEqual([1, 2])
    expect(chatCalls[0].body).toEqual({ message: 'Do tutors help?', context: null, sessionId: 'default' })
  })

  it('never starts overlapping requests on rapid repeated sends', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    await ready(result)
    act(() => {
      result.current.send('one')
      result.current.send('two')
      result.current.send('three')
    })
    expect(chatCalls).toHaveLength(1)
  })

  it('a delayed history response cannot overwrite a newer chat response', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    let p
    act(() => { p = result.current.send('fresh question') }) // history still loading
    await act(async () => { chatCalls[0].resolve(reply(5)); await p })
    await act(async () => { historyCalls[0].resolve({ messages: [msg(1, 'user', 'old'), msg(2, 'assistant', 'old a')] }) })
    expect(result.current.messages.map((m) => m.id)).toEqual([1, 2, 5, 6])
  })

  it('applies only the newest of two overlapping history loads, without duplicates', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    act(() => { result.current.reload() })
    expect(historyCalls).toHaveLength(2)
    await act(async () => { historyCalls[1].resolve({ messages: [msg(1, 'user', 'q'), msg(2, 'assistant', 'a')] }) })
    await act(async () => { historyCalls[0].resolve({ messages: [msg(1, 'user', 'stale')] }) }) // older, arrives last
    expect(result.current.messages.map((m) => [m.id, m.content])).toEqual([[1, 'q'], [2, 'a']])
  })

  it('A -> B -> A: a reply started in the first visit to A is ignored', async () => {
    const { result, rerender } = renderHook(({ sid }) => useChat(clientFor(sid)), { initialProps: { sid: 'a' } })
    await ready(result)
    let p
    act(() => { p = result.current.send('question in first A') })
    rerender({ sid: 'b' })
    rerender({ sid: 'a' })
    await act(async () => { historyCalls.at(-1).resolve({ messages: [] }) })
    await act(async () => { chatCalls[0].resolve(reply(1, 'a')); await p })
    expect(result.current.messages).toEqual([])
    expect(result.current.pending).toBeNull()
    // and the new generation can send normally (guard was not left locked)
    act(() => { result.current.send('again') })
    expect(chatCalls).toHaveLength(2)
  })

  it('a delayed upload note appears only in the session that started the upload', async () => {
    const { result, rerender } = renderHook(({ sid }) => useChat(clientFor(sid)), { initialProps: { sid: 'a' } })
    await ready(result)
    rerender({ sid: 'b' })
    await ready(result)
    act(() => { result.current.addNote('Upload finished: paper X', { forSession: 'a' }) })
    expect(result.current.messages).toEqual([]) // not shown in B
    rerender({ sid: 'a' })
    await ready(result)
    expect(result.current.messages.map((m) => [m.role, m.content])).toEqual([['status', 'Upload finished: paper X']])
  })

  it('a failed send releases the guard and can be retried', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    await ready(result)
    let p
    act(() => { p = result.current.send('Do tutors help?') })
    await act(async () => { chatCalls[0].reject(new Error('Gemini was unavailable')); await p })
    expect(result.current.error).toMatchObject({ kind: 'send', text: 'Do tutors help?' })
    expect(result.current.pending).toBeNull()
    expect(result.current.messages).toHaveLength(0)
    act(() => { p = result.current.retry() })
    expect(chatCalls).toHaveLength(2)
    await act(async () => { chatCalls[1].resolve(reply(1)); await p })
    expect(result.current.error).toBeNull()
    expect(result.current.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
  })

  it('keeps the pending question separate from server-confirmed assistant messages', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    await ready(result)
    act(() => { result.current.send('Why do Nodes 4 and 7 disagree?') })
    expect(result.current.messages.some((m) => m.role === 'assistant')).toBe(false)
    expect(result.current.pending).toMatchObject({ text: 'Why do Nodes 4 and 7 disagree?' })
  })
})

describe('useChat — clearing', () => {
  const withClear = (sid) => {
    const c = clientFor(sid)
    c.clearChat = vi.fn(() => Promise.resolve({ deleted: 2 }))
    return c
  }

  it('clear() deletes on the server, empties the conversation and drops this session\'s notes', async () => {
    const c = withClear('default')
    const { result } = renderHook(() => useChat(c))
    await act(async () => { historyCalls[0].resolve({ messages: [msg(1, 'user', 'q'), msg(2, 'assistant', 'a')] }) })
    act(() => { result.current.addNote('Upload finished.') })
    expect(result.current.messages).toHaveLength(3)
    await act(async () => { await result.current.clear() })
    expect(c.clearChat).toHaveBeenCalledTimes(1)
    expect(result.current.messages).toEqual([])
  })

  it('refuses to clear while an answer is pending (nothing is sent to the server)', async () => {
    const c = withClear('default')
    const { result } = renderHook(() => useChat(c))
    await ready(result)
    act(() => { result.current.send('Do tutors help?') })
    await expect(result.current.clear()).rejects.toThrow(/current answer/)
    expect(c.clearChat).not.toHaveBeenCalled()
  })

  it('after resetLocal, a late answer or a late history response cannot bring deleted messages back', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    act(() => { result.current.reload() })                      // history load #2 in flight
    let p
    act(() => { p = result.current.send('in flight') })          // answer in flight
    act(() => { result.current.resetLocal() })                   // e.g. all papers were cleared
    await act(async () => {
      chatCalls[0].resolve(reply(5))
      historyCalls[0].resolve({ messages: [msg(1, 'user', 'old')] })
      historyCalls[1].resolve({ messages: [msg(1, 'user', 'old')] })
      await p
    })
    expect(result.current.messages).toEqual([])
    expect(result.current.pending).toBeNull()
    // The guard was released: the user can ask again right away.
    let p2
    act(() => { p2 = result.current.send('next') })
    expect(chatCalls).toHaveLength(2)
    await act(async () => { chatCalls[1].resolve(reply(9)); await p2 })
    expect(result.current.messages.map((m) => m.id)).toEqual([9, 10])
  })

  it('a newer history load updates messages already shown (e.g. an "evidence removed" flag)', async () => {
    const { result } = renderHook(() => useChat(clientFor('default')))
    await act(async () => { historyCalls[0].resolve({ messages: [msg(1, 'user', 'q'), msg(2, 'assistant', 'a')] }) })
    act(() => { result.current.reload() })
    await act(async () => {
      historyCalls[1].resolve({ messages: [msg(1, 'user', 'q'), { ...msg(2, 'assistant', 'a'), evidence_removed: true }] })
    })
    expect(result.current.messages[1].evidence_removed).toBe(true)
  })

  it('reports a 409 data_changed (cleared elsewhere) so the workspace can re-sync', async () => {
    const onDataChanged = vi.fn()
    const { result } = renderHook(() => useChat(clientFor('default'), { onDataChanged }))
    await ready(result)
    let p
    act(() => { p = result.current.send('q') })
    const err = Object.assign(new Error('The conversation was cleared while this answer was being prepared.'),
      { detail: { code: 'data_changed', cleared: 'chat' } })
    await act(async () => { chatCalls[0].reject(err); await p })
    expect(onDataChanged).toHaveBeenCalledWith({ code: 'data_changed', cleared: 'chat' })
    expect(result.current.error.message).toMatch(/cleared/)
  })
})