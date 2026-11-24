import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, sessionApi } from '../api/client.js'

const client = sessionApi('aaaaaaaaaaaa') // the session these tests run in
import { useReanalysis } from './useReanalysis.js'

afterEach(() => vi.restoreAllMocks())

const PENDING = [{ id: 'lib-a', title: 'A', is_new: false }, { id: 'lib-b', title: 'B', is_new: false }, { id: 'new-d', title: 'D', is_new: true }]

describe('useReanalysis', () => {
  it('analyzes papers one at a time in the given order and refreshes after each', async () => {
    const order = []
    vi.spyOn(client, 'analyzePaper').mockImplementation(async (id) => { order.push(id) })
    const refresh = vi.fn(async () => {})
    const { result } = renderHook(() => useReanalysis({ client, refresh }))
    await act(async () => { await result.current.start(PENDING) })
    expect(order).toEqual(['lib-a', 'lib-b', 'new-d'])
    expect(refresh).toHaveBeenCalledTimes(3)
    expect(refresh).toHaveBeenCalledWith({ fallback: false })
    expect(result.current).toMatchObject({ running: false, done: 3, total: 3, error: null })
  })

  it('stops at the first failure and reports which paper is still pending', async () => {
    vi.spyOn(client, 'analyzePaper').mockImplementation(async (id) => {
      if (id === 'lib-b') throw new ApiError(503, 'Gemini was unavailable. The paper is kept and still awaits re-analysis.')
    })
    const { result } = renderHook(() => useReanalysis({ client, refresh: async () => {} }))
    await act(async () => { await result.current.start(PENDING) })
    expect(client.analyzePaper).toHaveBeenCalledTimes(2)
    expect(result.current.error.paper.id).toBe('lib-b')
    expect(result.current.error.message).toMatch(/kept/)
    expect(result.current.done).toBe(1)
  })

  it('ignores a second start while running and treats "already analyzed" as done', async () => {
    let release
    vi.spyOn(client, 'analyzePaper')
      .mockImplementationOnce(() => new Promise((r) => { release = r }))
      .mockImplementation(async () => { throw new ApiError(409, { code: 'already_analyzed', message: 'done' }) })
    const { result } = renderHook(() => useReanalysis({ client, refresh: async () => {} }))
    let p
    act(() => { p = result.current.start(PENDING) })
    await act(async () => { expect(await result.current.start(PENDING)).toBe(false) })
    await act(async () => { release(); await p })
    expect(client.analyzePaper).toHaveBeenCalledTimes(3)
    expect(result.current).toMatchObject({ done: 3, error: null })
  })
})

describe('useReanalysis across a project switch', () => {
  it('uses only its own session client and starts no further paper after unmounting', async () => {
    let release
    const calls = []
    const client = {
      sessionId: 'aaaaaaaaaaaa',
      analyzePaper: vi.fn((id) => {
        calls.push(id)
        return id === 'lib-a' ? new Promise((r) => { release = r }) : Promise.resolve()
      }),
    }
    const { result, unmount } = renderHook(() => useReanalysis({ client, refresh: async () => {} }))
    let p
    act(() => { p = result.current.start(PENDING) })
    unmount() // the user switched to another project while paper A was being analyzed
    release() // paper A finishes on the server (in its own project)
    expect(await p).toBe(false)
    expect(calls).toEqual(['lib-a']) // B and D were not started; they stay pending in project A
    expect(client.analyzePaper).toHaveBeenCalledTimes(1)
  })
})