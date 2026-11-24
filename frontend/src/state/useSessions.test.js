import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, NetworkError } from '../api/client.js'
import { STORAGE_KEY, useSessions } from './useSessions.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const s = (id, title = id) => ({ id, title, title_source: 'auto', question: null, counts: { papers: 0 } })
const A = 'aaaaaaaaaaaa'
const B = 'bbbbbbbbbbbb'
const C = 'cccccccccccc'

let lists // each api.sessions() call gets its own deferred response, in call order

beforeEach(() => {
  lists = []
  window.localStorage.clear()
  vi.spyOn(api, 'sessions').mockImplementation(() => {
    const d = deferred()
    lists.push(d)
    return d.promise
  })
})
afterEach(() => vi.restoreAllMocks())

const stored = () => window.localStorage.getItem(STORAGE_KEY)

/** Mount the hook and resolve ITS initial list request (each call owns its own request index). */
async function ready(sessions = [s(A), s(B)]) {
  const index = lists.length
  const hook = renderHook(() => useSessions())
  await act(async () => {
    lists[index].resolve({ sessions })
  })
  await waitFor(() => expect(hook.result.current.status).toBe('ready'))
  return hook
}

describe('useSessions', () => {
  it('opens the remembered project if it still exists, else the most recent one', async () => {
    window.localStorage.setItem(STORAGE_KEY, B)
    const { result } = await ready([s(A), s(B)])
    expect(result.current.activeId).toBe(B)
    window.localStorage.setItem(STORAGE_KEY, 'dddddddddddd') // deleted elsewhere
    const again = await ready([s(A), s(B)])
    expect(again.result.current.activeId).toBe(A)
    expect(stored()).toBe(A)
  })

  it('keeps the project remembered under the old ResearchLens key and moves it to the new key', async () => {
    window.localStorage.setItem('researchlens.activeSession', B)
    const { result } = await ready([s(A), s(B)])
    expect(result.current.activeId).toBe(B)
    expect(stored()).toBe(B)
    expect(window.localStorage.getItem('researchlens.activeSession')).toBeNull()
  })

  it('a valid selection switches; an unknown id changes nothing', async () => {
    const { result } = await ready()
    act(() => { expect(result.current.select(B)).toBe(true) })
    expect(result.current.activeId).toBe(B)
    expect(stored()).toBe(B)
    act(() => { expect(result.current.select('dddddddddddd')).toBe(false) })
    expect(result.current.activeId).toBe(B)
    expect(stored()).toBe(B)
  })

  it('a refresh still in flight cannot switch the user back after a manual selection', async () => {
    vi.spyOn(api, 'createSession').mockResolvedValue(s(C, 'New project'))
    const { result } = await ready()
    let p
    act(() => { p = result.current.create() }) // the new project becomes active; its list refresh is pending
    await waitFor(() => expect(result.current.activeId).toBe(C))
    const pending = lists.length - 1
    act(() => { result.current.select(A) }) // the user switches before the refresh returns
    expect(result.current.activeId).toBe(A)
    await act(async () => { lists[pending].resolve({ sessions: [s(C), s(A), s(B)] }); await p })
    expect(result.current.activeId).toBe(A) // the stale refresh did not override the choice
    expect(stored()).toBe(A)
    expect(result.current.sessions.map((x) => x.id)).toContain(C) // the created project is still listed
  })

  it('an invalid selection does not invalidate a legitimate pending refresh', async () => {
    const { result } = await ready()
    let p
    act(() => { p = result.current.reload() })
    const pending = lists.length - 1
    act(() => { result.current.select('dddddddddddd') })
    await act(async () => { lists[pending].resolve({ sessions: [s(A), s(B), s(C)] }); await p })
    expect(result.current.sessions.map((x) => x.id)).toEqual([A, B, C]) // the refresh was applied
    expect(result.current.activeId).toBe(A)
  })

  it('renames through the server and keeps the active project', async () => {
    vi.spyOn(api, 'renameSession').mockResolvedValue({ ...s(B), title: 'Vocabulary', title_source: 'manual' })
    const { result } = await ready()
    act(() => { result.current.select(B) })
    let p
    act(() => { p = result.current.rename(B, 'Vocabulary') })
    await waitFor(() => expect(result.current.active.title).toBe('Vocabulary'))
    await act(async () => { lists.at(-1).resolve({ sessions: [{ ...s(B), title: 'Vocabulary' }, s(A)] }); await p })
    expect(api.renameSession).toHaveBeenCalledWith(B, 'Vocabulary')
    expect(result.current.activeId).toBe(B)
  })

  it('deleting the active project moves to a remaining one even if the refresh fails', async () => {
    vi.spyOn(api, 'deleteSession').mockResolvedValue({ deleted: B })
    const { result } = await ready()
    act(() => { result.current.select(B) })
    let p
    act(() => { p = result.current.remove(B) })
    await waitFor(() => expect(result.current.activeId).toBe(A))
    await waitFor(() => expect(lists).toHaveLength(2))
    expect(result.current.sessions.map((x) => x.id)).toEqual([A])
    await act(async () => { lists.at(-1).reject(new NetworkError(new Error('offline'))); await p })
    expect(result.current.activeId).toBe(A)
    expect(result.current.sessions.some((x) => x.id === B)).toBe(false)
    expect(stored()).toBe(A)
  })

  it('a failed delete changes nothing locally', async () => {
    vi.spyOn(api, 'deleteSession').mockRejectedValue(new ApiError(409, { code: 'confirmation_required', message: 'no' }))
    const { result } = await ready()
    act(() => { result.current.select(B) })
    await act(async () => { await expect(result.current.remove(B)).rejects.toThrow('no') })
    expect(result.current.activeId).toBe(B)
    expect(result.current.sessions.map((x) => x.id)).toEqual([A, B])
  })

  it('deleting the last project leaves no active project', async () => {
    vi.spyOn(api, 'deleteSession').mockResolvedValue({ deleted: A })
    const { result } = await ready([s(A)])
    let p
    act(() => { p = result.current.remove(A) })
    await waitFor(() => expect(lists).toHaveLength(2)) // the refresh starts after the server confirmed the delete
    await act(async () => { lists[1].resolve({ sessions: [] }); await p })
    expect(result.current.activeId).toBeNull()
    expect(stored()).toBeNull()
  })

  it('reports an unreachable server as offline', async () => {
    const { result } = renderHook(() => useSessions())
    await act(async () => { lists[0].reject(new NetworkError(new Error('down'))) })
    expect(result.current.status).toBe('error')
    expect(result.current.offline).toBe(true)
  })
})