import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SESSION_HEADER, sessionApi } from '../api/client.js'
import { fixtureSource, useResearch, workspaceFromGraph } from './useResearch.js'

const CLIENT = sessionApi('aaaaaaaaaaaa') // stable identity, like the memoized client in the app

const json = (status, body) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
const LIVE_GRAPH = { question: 'Q?', meta: { source: 'live' }, nodes: [], edges: [], papers: {}, claims: {}, relationships: {} }
const SAMPLE_GRAPH = { ...LIVE_GRAPH, meta: { source: 'sample' }, papers: { a: { is_new: false } } }

function routeFetch(routes) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
    for (const [prefix, handler] of Object.entries(routes)) if (String(url).startsWith(prefix)) return handler()
    return json(404, { detail: 'not found' })
  })
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('fixture provenance', () => {
  it('treats fixtures as fictional unless explicitly marked as a real snapshot', () => {
    expect(fixtureSource({ meta: { source: 'sample' } })).toBe('sample')
    expect(fixtureSource({ meta: {} })).toBe('sample')
    expect(fixtureSource({})).toBe('sample')
    expect(fixtureSource({ meta: { source: 'live' } })).toBe('sample')
    expect(fixtureSource({ meta: { source: 'snapshot' } })).toBe('snapshot')
    expect(workspaceFromGraph(SAMPLE_GRAPH, 'sample').source).toBe('sample')
  })
})

describe('useResearch', () => {
  it('uses live data and survives an optional impact-endpoint failure', async () => {
    routeFetch({
      '/api/graph': () => json(200, LIVE_GRAPH),
      '/api/workspace': () => json(200, { question: 'Q?', has_library: false, counts: {} }),
      '/api/impact/latest': () => json(500, { detail: 'boom' }),
      '/api/checks': () => json(500, {}),
      '/api/health': () => json(200, { database: true }),
    })
    const { result } = renderHook(() => useResearch(CLIENT))
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.source).toBe('live')
    for (const [, init] of globalThis.fetch.mock.calls) expect(init.headers[SESSION_HEADER]).toBe('aaaaaaaaaaaa')
    expect(result.current.impact).toBeNull()
    expect(result.current.checks).toBeNull()
  })

  it('never substitutes sample data for a live project when the server is unreachable', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).startsWith('/api/')) return Promise.reject(new TypeError('Failed to fetch'))
      if (String(url).includes('graph_with_new.json')) return json(200, SAMPLE_GRAPH)
      return json(404, null)
    })
    const { result } = renderHook(() => useResearch(CLIENT))
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.graph).toBeNull()
    expect(result.current.source).toBeNull()
    expect(spy.mock.calls.some(([url]) => String(url).includes('/fixtures/'))).toBe(false)
  })

  it('keeps the real data already shown when a later refresh fails', async () => {
    let up = true
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (!up) return Promise.reject(new TypeError('Failed to fetch'))
      if (String(url).startsWith('/api/graph')) return json(200, LIVE_GRAPH)
      if (String(url).startsWith('/api/workspace')) return json(200, { question: 'Q?', counts: {} })
      return json(200, {})
    })
    const { result } = renderHook(() => useResearch(CLIENT))
    await waitFor(() => expect(result.current.status).toBe('ready'))
    up = false
    await result.current.refresh()
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.graph).toEqual(LIVE_GRAPH)
    expect(result.current.source).toBe('live')
  })

  it('shows labelled sample data only in explicitly chosen demo mode', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).includes('graph_with_new.json')) return json(200, SAMPLE_GRAPH)
      return json(404, null)
    })
    const { result } = renderHook(() => useResearch(null, { demo: true }))
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.source).toBe('sample')
    expect(result.current.workspace.source).toBe('sample')
    expect(spy.mock.calls.some(([url]) => String(url).startsWith('/api/'))).toBe(false)
  })

  it('without a project and without demo mode it loads nothing at all', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => json(404, null))
    const { result } = renderHook(() => useResearch(null))
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.graph).toBeNull()
    expect(spy).not.toHaveBeenCalled()
  })
})