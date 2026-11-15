import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./components/GraphCanvas.jsx', () => ({ default: () => <div data-testid="graph-canvas" /> }))
const { default: App } = await import('./App.jsx')
const { default: SessionSwitcher } = await import('./components/workspace/SessionSwitcher.jsx')

const A = 'aaaaaaaaaaaa'
const B = 'bbbbbbbbbbbb'
const session = (id, title, question) => ({ id, title, title_source: 'auto', question, counts: { papers: 1, claims: 2 } })
const json = (status, body) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
const SAMPLE = {
  question: 'Do AI tutors improve learning?', meta: { source: 'sample' },
  nodes: [{ data: { id: 'q', type: 'question', label: 'Q' } }], edges: [], papers: {}, claims: {}, relationships: {},
}

afterEach(() => {
  window.localStorage.clear()
  cleanup()
  vi.restoreAllMocks()
})

describe('App data provenance', () => {
  it('an unreachable server shows a connection problem, never sample evidence', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).startsWith('/api/')) return Promise.reject(new TypeError('Failed to fetch'))
      if (String(url).includes('graph_with_new.json')) return json(200, SAMPLE)
      return json(404, null)
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Unable to connect to ResearchLens' })).toBeTruthy()
    expect(screen.queryByText(/Sample data/)).toBeNull()
    expect(screen.queryByText('No projects yet')).toBeNull()
    expect(spy.mock.calls.some(([url]) => String(url).includes('/fixtures/'))).toBe(false)
    expect(screen.getByRole('button', { name: 'Retry connection' })).toBeTruthy()
  })

  it('retrying reconnects to the real projects', async () => {
    let up = false
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (!up) return Promise.reject(new TypeError('Failed to fetch'))
      if (String(url).startsWith('/api/sessions')) return json(200, { sessions: [session(A, 'Tutoring project', 'Q?')] })
      if (String(url).startsWith('/api/graph')) return json(200, { ...SAMPLE, meta: { source: 'live' } })
      if (String(url).startsWith('/api/workspace')) return json(200, { question: 'Q?', counts: {}, has_library: false })
      return json(404, { detail: 'none' })
    })
    render(<App />)
    await screen.findByRole('heading', { name: 'Unable to connect to ResearchLens' })
    up = true
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry connection' })) })
    expect(await screen.findByTestId('session-switcher')).toBeTruthy()
    expect(screen.getByTestId('session-switcher').textContent).toContain('Tutoring project')
  })

  it('a database that still needs migration shows a maintenance message, not a connection error', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => (String(url).startsWith('/api/')
      ? json(503, { detail: { code: 'migration_required', message: 'migrate first' } }) : json(404, null)))
    render(<App />)
    expect(await screen.findByRole('heading', { name: /needs a one-time database upgrade/ })).toBeTruthy()
    expect(screen.queryByText('Unable to connect to ResearchLens')).toBeNull()
    expect(screen.getByText(/Your research has not been changed/)).toBeTruthy()
  })

  it('fictional demo data appears only when explicitly chosen, clearly labelled, and can be exited', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).startsWith('/api/')) return Promise.reject(new TypeError('Failed to fetch'))
      if (String(url).includes('graph_with_new.json')) return json(200, SAMPLE)
      return json(404, null)
    })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Explore fictional demo data instead' }))
    const banner = await screen.findByText('Demo mode — sample data (fictional)')
    expect(banner.closest('[role="status"]').textContent).toMatch(/Nothing here is real research evidence/)
    fireEvent.click(screen.getByRole('button', { name: 'Exit demo' }))
    expect(await screen.findByRole('heading', { name: 'Unable to connect to ResearchLens' })).toBeTruthy()
  })

  it('shows no sample banner for live data', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (String(url).startsWith('/api/sessions')) return json(200, { sessions: [session(A, 'Project A', 'Q?')] })
      if (String(url).startsWith('/api/graph')) return json(200, { ...SAMPLE, meta: { source: 'live' } })
      if (String(url).startsWith('/api/workspace')) return json(200, { question: 'Q?', counts: {}, has_library: false })
      if (String(url).startsWith('/api/checks')) return json(200, { ok: true, violations: { a: [] } })
      return json(404, { detail: 'none' })
    })
    render(<App />)
    await screen.findByText(/Citation Integrity Passed/)
    expect(screen.queryByText('Sample data (fictional)')).toBeNull()
  })
})


function deferred() {
  let resolve
  const promise = new Promise((res) => { resolve = res })
  return { promise, resolve }
}

const graphFor = (question, paper) => ({
  question, meta: { source: 'live' }, nodes: [{ data: { id: 'q', type: 'question', label: 'Central Question' } }],
  edges: [], papers: { [paper]: { id: paper, title: paper, is_new: false, access_level: 'full' } }, claims: {}, relationships: {},
})

describe('research projects', () => {
  // Per-session server: graph responses for A can be held back to arrive late.
  function server({ holdA = false } = {}) {
    const late = deferred()
    const calls = []
    vi.spyOn(globalThis, 'fetch').mockImplementation((url, init = {}) => {
      const sid = init.headers?.['X-Session-Id']
      calls.push([String(url), sid])
      const u = String(url)
      if (u.startsWith('/api/sessions')) return json(200, { sessions: [session(A, 'Tutoring project', 'Do AI tutors help?'), session(B, 'Vocabulary project', 'Does spacing help?')] })
      if (u.startsWith('/api/graph')) {
        if (sid === A) return holdA ? late.promise.then(() => json(200, graphFor('Do AI tutors help?', 'Paper from A'))) : json(200, graphFor('Do AI tutors help?', 'Paper from A'))
        if (sid === B) return json(200, graphFor('Does spacing help?', 'Paper from B'))
      }
      if (u.startsWith('/api/workspace')) {
        const q = sid === A ? 'Do AI tutors help?' : 'Does spacing help?'
        return json(200, { question: q, counts: { papers: 1, library_papers: 1, claims: 0 }, has_library: true, pending_papers: [] })
      }
      if (u.startsWith('/api/chat/messages')) return json(200, { messages: [] })
      if (u.startsWith('/api/checks')) return json(200, { ok: true, violations: {} })
      return json(404, { detail: 'none' })
    })
    return { late, calls }
  }

  it('opens the most recent project and sends its id with every research request', async () => {
    const { calls } = server()
    render(<App />)
    expect(await screen.findByText('Paper from A')).toBeTruthy()
    const research = calls.filter(([u]) => /\/api\/(graph|workspace|checks|chat|impact)/.test(u))
    expect(research.length).toBeGreaterThan(0)
    expect(research.every(([, sid]) => sid === A)).toBe(true)
    expect(screen.getByTestId('session-switcher').textContent).toContain('Tutoring project')
  })

  it('a late response from the previous project never appears in the new one', async () => {
    const { late } = server({ holdA: true })
    render(<App />)
    await screen.findByTestId('session-switcher')
    fireEvent.click(screen.getByTestId('session-switcher'))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Vocabulary project/ }))
    expect(await screen.findByText('Paper from B')).toBeTruthy()
    await act(async () => { late.resolve() }) // A's graph arrives after the switch
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByText('Paper from A')).toBeNull()
    expect(screen.getByText('Paper from B')).toBeTruthy()
    expect(window.localStorage.getItem('researchlens.activeSession')).toBe(B)
  })

  it('switching back and forth shows each project\'s own data, and the choice survives a reload', async () => {
    server()
    const { unmount } = render(<App />)
    await screen.findByText('Paper from A')
    for (const [name, paper, other] of [[/Vocabulary/, 'Paper from B', 'Paper from A'], [/Tutoring/, 'Paper from A', 'Paper from B'],
      [/Vocabulary/, 'Paper from B', 'Paper from A']]) {
      fireEvent.click(screen.getByTestId('session-switcher'))
      fireEvent.click(screen.getByRole('menuitemradio', { name }))
      await screen.findByText(paper)
      expect(screen.queryByText(other)).toBeNull()
    }
    unmount() // "refresh the browser"
    render(<App />)
    expect(await screen.findByText('Paper from B')).toBeTruthy()
    expect(screen.queryByText('Paper from A')).toBeNull()
  })

  it('offers to create the first project when none exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => (String(url).startsWith('/api/sessions')
      ? json(200, { sessions: [] }) : json(404, { detail: 'none' })))
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Create your first research project' })).toBeTruthy()
  })
})

describe('project switcher safety', () => {
  const list = [session(A, 'Tutoring project', 'Q1?'), session(B, 'Vocabulary project', 'Q2?')]
  const busy = { label: 'A re-analysis', detail: 'The paper being analyzed now finishes on the server.' }

  it('leaving a busy project (switching or creating) always needs "Switch anyway"', () => {
    const onSelect = vi.fn()
    const onCreate = vi.fn(async () => {})
    render(<SessionSwitcher sessions={list} active={list[0]} busy={busy} onSelect={onSelect} onCreate={onCreate}
      onRename={vi.fn()} onDelete={vi.fn()} />)
    fireEvent.click(screen.getByTestId('session-switcher'))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Vocabulary/ }))
    expect(onSelect).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toMatch(/A re-analysis is running in this project/)
    fireEvent.click(screen.getByRole('button', { name: '+ New research project' })) // with a switch already pending
    expect(onCreate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Switch anyway' }))
    expect(onCreate).toHaveBeenCalledTimes(1)
    expect(onSelect).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Delete project…', hidden: true })).toBeTruthy()
  })

  it('deleting needs an explicit acknowledgement and targets the project chosen when the dialog opened', async () => {
    const onDelete = vi.fn(async () => {})
    const { rerender } = render(<SessionSwitcher sessions={list} active={list[0]} onSelect={vi.fn()} onCreate={vi.fn()}
      onRename={vi.fn()} onDelete={onDelete} />)
    fireEvent.click(screen.getByTestId('session-switcher'))
    fireEvent.click(screen.getByRole('button', { name: 'Delete project…' }))
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toMatch(/Tutoring project/)
    expect(dialog.textContent).toMatch(/permanently deletes/)
    const confirm = screen.getByRole('button', { name: 'Delete project permanently' })
    expect(confirm.disabled).toBe(true)
    rerender(<SessionSwitcher sessions={list} active={list[1]} onSelect={vi.fn()} onCreate={vi.fn()}
      onRename={vi.fn()} onDelete={onDelete} />) // the active project changes while the dialog is open
    fireEvent.click(screen.getByRole('checkbox'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete project permanently' })) })
    expect(onDelete).toHaveBeenCalledWith(A) // still the project the user chose
  })

  it('cannot delete a project while work is running in it', () => {
    render(<SessionSwitcher sessions={list} active={list[0]} busy={busy} onSelect={vi.fn()} onCreate={vi.fn()}
      onRename={vi.fn()} onDelete={vi.fn()} />)
    fireEvent.click(screen.getByTestId('session-switcher'))
    expect(screen.getByRole('button', { name: 'Delete project…' }).disabled).toBe(true)
  })

  it('renames inline and can return to the automatic title', async () => {
    const onRename = vi.fn(async () => {})
    render(<SessionSwitcher sessions={list} active={{ ...list[0], title_source: 'manual' }} onSelect={vi.fn()}
      onCreate={vi.fn()} onRename={onRename} onDelete={vi.fn()} />)
    fireEvent.click(screen.getByTestId('session-switcher'))
    fireEvent.click(screen.getByRole('button', { name: 'Rename project…' }))
    fireEvent.change(screen.getByLabelText('Project title'), { target: { value: 'My tutoring review' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(onRename).toHaveBeenCalledWith(A, 'My tutoring review')
    await waitFor(() => expect(screen.queryByLabelText('Project title')).toBeNull())
  })
})
