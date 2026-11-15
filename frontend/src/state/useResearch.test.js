import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fixtureSource, useResearch, workspaceFromGraph } from './useResearch.js'

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
    const { result } = renderHook(() => useResearch())
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.source).toBe('live')
    expect(result.current.impact).toBeNull()
    expect(result.current.checks).toBeNull()
  })

  it('falls back to fixtures labelled as sample data when the server is unreachable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).startsWith('/api/')) return Promise.reject(new TypeError('Failed to fetch'))
      if (String(url).includes('graph_with_new.json')) return json(200, SAMPLE_GRAPH)
      return json(404, null)
    })
    const { result } = renderHook(() => useResearch())
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.source).toBe('sample')
    expect(result.current.workspace.source).toBe('sample')
  })
})
