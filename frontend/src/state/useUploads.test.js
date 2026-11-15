import { createHash } from 'node:crypto'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, NetworkError, TimeoutError, api } from '../api/client.js'
import { MESSAGES, STATUS, sha256Hex, useUploads } from './useUploads.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const pdf = (name, size = 100) => new File([new Uint8Array(size).fill(size % 251)], name,
  { type: 'application/pdf', lastModified: 1 })
const hashOf = (file) => `sha-${file.name}` // deterministic stand-in fingerprint

let uploads
let lookups
beforeEach(() => {
  uploads = []
  lookups = []
  vi.spyOn(api, 'uploadPaper').mockImplementation((file, mode) => {
    const d = deferred()
    uploads.push({ file, mode, ...d })
    return d.promise
  })
  vi.spyOn(api, 'lookupPaper').mockImplementation((sha) => {
    const d = deferred()
    lookups.push({ sha, ...d })
    return d.promise
  })
})
afterEach(() => vi.restoreAllMocks())

function setup({ refresh } = {}) {
  const refreshFn = refresh ?? vi.fn(async () => ({}))
  const onSettled = vi.fn()
  const hook = renderHook(() => useUploads({ refresh: refreshFn, sessionId: 's1', onSettled, hashFile: async (f) => hashOf(f) }))
  return { ...hook, refresh: refreshFn, onSettled }
}

async function startOne(result, name = 'a.pdf', mode = 'library') {
  act(() => { result.current.add([pdf(name)], mode) })
  await waitFor(() => expect(uploads).toHaveLength(1))
}
const item = (result, i = 0) => result.current.items[i]

describe('useUploads — queue and validation', () => {
  it('uploads one file at a time with the chosen mode, then refreshes without fixture fallback', async () => {
    const { result, refresh } = setup()
    act(() => { result.current.add([pdf('a.pdf'), pdf('b.pdf', 200)], 'library') })
    await waitFor(() => expect(uploads).toHaveLength(1))
    expect(uploads[0].mode).toBe('library')
    expect(result.current.items.map((x) => x.status)).toEqual([STATUS.analyzing, STATUS.queued])
    await act(async () => { uploads[0].resolve({ mode: 'library', claims_kept: 3, relationships_created: 2 }) })
    await waitFor(() => expect(uploads).toHaveLength(2))
    expect(refresh).toHaveBeenCalledWith({ fallback: false })
    expect(item(result).status).toBe(STATUS.done)
  })

  it('rejects non-PDFs, queue duplicates, and a second concurrent evaluation', () => {
    const { result } = setup()
    let out
    act(() => { out = result.current.add([pdf('a.pdf'), new File(['x'], 'notes.txt', { type: 'text/plain' })], 'library') })
    expect(out.accepted).toEqual(['a.pdf'])
    expect(out.rejected[0].reason).toMatch(/Only PDF/)
    act(() => { out = result.current.add([pdf('a.pdf')], 'evaluate') })
    expect(out.rejected[0].reason).toMatch(/already added/)
    act(() => { out = result.current.add([pdf('d.pdf', 7), pdf('e.pdf', 8)], 'evaluate') })
    expect(out.accepted).toEqual(['d.pdf'])
    expect(out.rejected[0].reason).toMatch(/one new paper at a time/)
  })

  it('shows the server duplicate error and reports it once with the starting session', async () => {
    const { result, onSettled } = setup()
    await startOne(result)
    await act(async () => {
      uploads[0].reject(new ApiError(409, { message: 'This PDF is already in the evidence graph', code: 'duplicate' }))
    })
    await waitFor(() => expect(item(result).status).toBe(STATUS.failed))
    expect(item(result)).toMatchObject({ duplicate: true, message: 'This PDF is already in the evidence graph' })
    expect(onSettled).toHaveBeenCalledTimes(1)
    expect(onSettled).toHaveBeenCalledWith(expect.objectContaining({ status: STATUS.failed, sessionId: 's1' }))
  })
})

describe('useUploads — success is independent of the graph refresh', () => {
  it('stays done when the refresh fails, and retrying the refresh never re-uploads', async () => {
    const refresh = vi.fn().mockRejectedValueOnce(new Error('graph down')).mockResolvedValue({})
    const { result } = setup({ refresh })
    await startOne(result)
    const confirmed = { mode: 'library', paper: { id: 'paper-x', title: 'X' }, claims_kept: 2 }
    await act(async () => { uploads[0].resolve(confirmed) })
    await waitFor(() => expect(item(result).refreshFailed).toBe(true))
    expect(item(result).status).toBe(STATUS.done)
    expect(item(result).result).toEqual(confirmed)
    expect(MESSAGES.refreshFailed).toMatch(/uploaded successfully, but the graph could not be refreshed/)
    await act(async () => { await result.current.retryRefresh(item(result).key) })
    expect(item(result).refreshFailed).toBe(false)
    expect(item(result).result).toEqual(confirmed)
    expect(api.uploadPaper).toHaveBeenCalledTimes(1)
  })
})

describe('useUploads — reconciliation by exact file hash', () => {
  it('timeout: done only when THIS file is confirmed complete on the server', async () => {
    const { result, onSettled } = setup()
    await startOne(result, 'a.pdf', 'evaluate')
    await act(async () => { uploads[0].reject(new TimeoutError(180000)) })
    await waitFor(() => expect(lookups).toHaveLength(1))
    expect(lookups[0].sha).toBe('sha-a.pdf')
    expect(item(result)).toMatchObject({ status: STATUS.checking, message: MESSAGES.checking })
    await act(async () => {
      lookups[0].resolve({ found: true, status: 'complete', paper: { id: 'a-123', title: 'Paper A' } })
    })
    await waitFor(() => expect(item(result).status).toBe(STATUS.done))
    expect(item(result)).toMatchObject({ fromServer: true, result: { paper: { id: 'a-123' } } })
    expect(onSettled).toHaveBeenCalledWith(expect.objectContaining({ status: STATUS.done, sessionId: 's1' }))
  })

  it('an unrelated new paper in the graph never marks the timed-out upload done', async () => {
    // The graph refresh would show a new paper, but the lookup for THIS file finds nothing.
    const refresh = vi.fn(async () => ({ graph: { papers: { someone_else: { id: 'someone_else' } } } }))
    const { result } = setup({ refresh })
    await startOne(result)
    await act(async () => { uploads[0].reject(new TimeoutError(180000)) })
    await waitFor(() => expect(lookups).toHaveLength(1))
    await act(async () => { lookups[0].resolve({ found: false, status: null, paper: null }) })
    await waitFor(() => expect(item(result).status).toBe(STATUS.unknown))
    expect(item(result)).toMatchObject({ serverState: 'absent', message: MESSAGES.absent, fromServer: false })
  })

  it('still analyzing: stays unknown; "Check again" rechecks the same file without re-uploading', async () => {
    const { result } = setup()
    await startOne(result)
    await act(async () => { uploads[0].reject(new TimeoutError(180000)) })
    await waitFor(() => expect(lookups).toHaveLength(1))
    await act(async () => { lookups[0].resolve({ found: true, status: 'processing', paper: { id: 'a-1' } }) })
    await waitFor(() => expect(item(result).status).toBe(STATUS.unknown))
    expect(item(result).message).toBe(MESSAGES.processing)
    expect(result.current.canRetry(item(result))).toBe(false) // never re-upload while the server is working
    act(() => { result.current.checkAgain(item(result).key) })
    expect(lookups).toHaveLength(2)
    expect(lookups[1].sha).toBe('sha-a.pdf')
    await act(async () => { lookups[1].resolve({ found: true, status: 'complete', paper: { id: 'a-1' } }) })
    await waitFor(() => expect(item(result).status).toBe(STATUS.done))
    expect(api.uploadPaper).toHaveBeenCalledTimes(1)
  })

  it('network interruption is uncertain: it reconciles instead of failing', async () => {
    const { result } = setup()
    await startOne(result)
    await act(async () => { uploads[0].reject(new NetworkError(new TypeError('Failed to fetch'))) })
    await waitFor(() => expect(lookups).toHaveLength(1))
    expect(item(result).status).toBe(STATUS.checking)
    await act(async () => { lookups[0].resolve({ found: true, status: 'complete', paper: { id: 'a-9' } }) })
    await waitFor(() => expect(item(result).status).toBe(STATUS.done))
  })

  it('a failed reconciliation ends in unknown (not stuck in checking) and can be checked again', async () => {
    const { result } = setup()
    await startOne(result)
    await act(async () => { uploads[0].reject(new NetworkError(new TypeError('offline'))) })
    await waitFor(() => expect(lookups).toHaveLength(1))
    await act(async () => { lookups[0].reject(new NetworkError(new TypeError('still offline'))) })
    await waitFor(() => expect(item(result).status).toBe(STATUS.unknown))
    expect(item(result)).toMatchObject({ serverState: 'unreachable', message: MESSAGES.unreachable })
    act(() => { result.current.checkAgain(item(result).key) })
    expect(lookups).toHaveLength(2)
  })

  it('rapid "Check again" clicks start only one reconciliation', async () => {
    const { result } = setup()
    await startOne(result)
    await act(async () => { uploads[0].reject(new TimeoutError(180000)) })
    await waitFor(() => expect(lookups).toHaveLength(1))
    await act(async () => { lookups[0].reject(new Error('down')) })
    await waitFor(() => expect(item(result).status).toBe(STATUS.unknown))
    act(() => {
      result.current.checkAgain(item(result).key)
      result.current.checkAgain(item(result).key)
      result.current.checkAgain(item(result).key)
    })
    expect(lookups).toHaveLength(2)
  })

  it('an explicit retry re-uploads through the server duplicate protection', async () => {
    const { result } = setup()
    await startOne(result)
    await act(async () => { uploads[0].reject(new TimeoutError(180000)) })
    await waitFor(() => expect(lookups).toHaveLength(1))
    await act(async () => { lookups[0].resolve({ found: false, status: null, paper: null }) })
    await waitFor(() => expect(item(result).status).toBe(STATUS.unknown))
    act(() => { result.current.retry(item(result).key) })
    await waitFor(() => expect(uploads).toHaveLength(2))
    // Meanwhile the first attempt finished server-side: the server refuses to overwrite it.
    await act(async () => {
      uploads[1].reject(new ApiError(409, { message: 'This PDF is already in the evidence graph', code: 'duplicate' }))
    })
    await waitFor(() => expect(item(result).status).toBe(STATUS.failed))
    expect(item(result).duplicate).toBe(true)
  })
})

describe('sha256Hex', () => {
  it('matches the server-side hashlib.sha256 of the same bytes', async () => {
    const file = pdf('x.pdf', 300) // 300 bytes, each 300 % 251 = 49
    const expected = createHash('sha256').update(Buffer.alloc(300, 49)).digest('hex')
    expect(await sha256Hex(file)).toBe(expected)
  })
})
