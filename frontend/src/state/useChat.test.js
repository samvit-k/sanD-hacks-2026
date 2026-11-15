import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client.js'
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

beforeEach(() => {
  historyCalls = []
  chatCalls = []
  vi.spyOn(api, 'chatHistory').mockImplementation((sid) => {
    const d = deferred()
    historyCalls.push({ sid, ...d })
    return d.promise
  })
  vi.spyOn(api, 'chat').mockImplementation((body) => {
    const d = deferred()
    chatCalls.push({ body, ...d })
    return d.promise
  })
})

afterEach(() => vi.restoreAllMocks())

async function ready(result) {
  await act(async () => { historyCalls.at(-1).resolve({ messages: [] }) })
  await waitFor(() => expect(result.current.loaded).toBe(true))
}

describe('useChat', () => {
  it('sends a message and shows the confirmed pair exactly once', async () => {
    const { result } = renderHook(() => useChat('default'))
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
    const { result } = renderHook(() => useChat('default'))
    await ready(result)
    act(() => {
      result.current.send('one')
      result.current.send('two')
      result.current.send('three')
    })
    expect(chatCalls).toHaveLength(1)
  })

  it('a delayed history response cannot overwrite a newer chat response', async () => {
    const { result } = renderHook(() => useChat('default'))
    let p
    act(() => { p = result.current.send('fresh question') }) // history still loading
    await act(async () => { chatCalls[0].resolve(reply(5)); await p })
    await act(async () => { historyCalls[0].resolve({ messages: [msg(1, 'user', 'old'), msg(2, 'assistant', 'old a')] }) })
    expect(result.current.messages.map((m) => m.id)).toEqual([1, 2, 5, 6])
  })

  it('applies only the newest of two overlapping history loads, without duplicates', async () => {
    const { result } = renderHook(() => useChat('default'))
    act(() => { result.current.reload() })
    expect(historyCalls).toHaveLength(2)
    await act(async () => { historyCalls[1].resolve({ messages: [msg(1, 'user', 'q'), msg(2, 'assistant', 'a')] }) })
    await act(async () => { historyCalls[0].resolve({ messages: [msg(1, 'user', 'stale')] }) }) // older, arrives last
    expect(result.current.messages.map((m) => [m.id, m.content])).toEqual([[1, 'q'], [2, 'a']])
  })

  it('A -> B -> A: a reply started in the first visit to A is ignored', async () => {
    const { result, rerender } = renderHook(({ sid }) => useChat(sid), { initialProps: { sid: 'a' } })
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
    const { result, rerender } = renderHook(({ sid }) => useChat(sid), { initialProps: { sid: 'a' } })
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
    const { result } = renderHook(() => useChat('default'))
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
    const { result } = renderHook(() => useChat('default'))
    await ready(result)
    act(() => { result.current.send('Why do Nodes 4 and 7 disagree?') })
    expect(result.current.messages.some((m) => m.role === 'assistant')).toBe(false)
    expect(result.current.pending).toMatchObject({ text: 'Why do Nodes 4 and 7 disagree?' })
  })
})
