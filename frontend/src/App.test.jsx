import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./components/GraphCanvas.jsx', () => ({ default: () => <div data-testid="graph-canvas" /> }))
const { default: App } = await import('./App.jsx')

const json = (status, body) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
const SAMPLE = {
  question: 'Do AI tutors improve learning?', meta: { source: 'sample' },
  nodes: [{ data: { id: 'q', type: 'question', label: 'Q' } }], edges: [], papers: {}, claims: {}, relationships: {},
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('App data provenance', () => {
  it('prominently labels fictional fallback data', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).startsWith('/api/')) return Promise.reject(new TypeError('Failed to fetch'))
      if (String(url).includes('graph_with_new.json')) return json(200, SAMPLE)
      return json(404, null)
    })
    render(<App />)
    const banner = await screen.findByText('Sample data (fictional)')
    expect(banner.closest('[role="status"]').textContent).toMatch(/Nothing here is real research evidence/)
  })

  it('shows no sample banner for live data', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).startsWith('/api/graph')) return json(200, { ...SAMPLE, meta: { source: 'live' } })
      if (String(url).startsWith('/api/workspace')) return json(200, { question: 'Q?', counts: {}, has_library: false })
      if (String(url).startsWith('/api/checks')) return json(200, { ok: true, violations: { a: [] } })
      return json(404, { detail: 'none' })
    })
    render(<App />)
    await screen.findByText(/citation integrity passed/)
    expect(screen.queryByText('Sample data (fictional)')).toBeNull()
  })
})
