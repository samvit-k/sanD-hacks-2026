import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import TopBar, { checksStatus } from './TopBar.jsx'

afterEach(() => cleanup())

const clean = { ok: true, violations: { claims_with_bad_citations: [], impacts_with_bad_citations: [] } }

describe('checksStatus', () => {
  it('passes only for a well-formed, clean result', () => {
    expect(checksStatus(clean)).toEqual({ state: 'passed', issues: 0 })
  })

  it('reports violations', () => {
    expect(checksStatus({ ok: false, violations: { claims_with_bad_citations: [3, 4], x: [1] } }))
      .toEqual({ state: 'issues', issues: 3 })
  })

  it('treats missing or malformed results as unavailable, never as passed', () => {
    for (const bad of [null, undefined, {}, { ok: true }, { ok: true, violations: null }, { ok: true, violations: {} },
      { ok: true, violations: [] }, { ok: true, violations: { a: 'none' } }, { ok: 'yes', violations: { a: [] } },
      { ok: false, violations: { a: [] } }]) {
      expect(checksStatus(bad).state).toBe('unavailable')
    }
  })
})

describe('TopBar checks pill', () => {
  const props = { graph: null, workspace: null, health: null, query: '', onQuery: () => {}, onPick: () => {},
    filters: { labels: {}, insufficient: true, structural: true, discovery: true, onlyNew: false }, onFilters: () => {} }

  it('says "Citation Integrity Passed" with a tooltip that disclaims scientific truth', () => {
    render(<TopBar {...props} source="live" checks={clean} />)
    const pill = screen.getByText(/Citation Integrity Passed/)
    expect(pill.closest('.pill').title).toMatch(/does not assess whether the scientific claims are correct/)
    expect(screen.queryByText(/citations verified/)).toBeNull()
  })

  it('shows "Checks Unavailable" for malformed data', () => {
    render(<TopBar {...props} source="live" checks={{ ok: true }} />)
    expect(screen.getByText('Checks Unavailable')).toBeTruthy()
  })

  it('keeps the violation warning', () => {
    render(<TopBar {...props} source="live" checks={{ ok: false, violations: { a: [1, 2] } }} />)
    expect(screen.getByText(/Citation Integrity Issues/)).toBeTruthy()
  })
})

describe('TopBar heading', () => {
  const props = { graph: null, health: null, query: '', onQuery: () => {}, onPick: () => {},
    filters: { labels: {}, insufficient: true, structural: true, discovery: true, onlyNew: false }, onFilters: () => {} }
  it('shows the project title (follows renames) and keeps the question in the tooltip', () => {
    const ws = { question: 'Do intelligent tutoring systems improve student learning outcomes?', counts: {} }
    const { rerender } = render(<TopBar {...props} source="live" workspace={ws} projectTitle="Tutoring review" />)
    const h = screen.getByTestId('project-heading')
    expect(h.textContent).toBe('Tutoring review')
    expect(h.getAttribute('title')).toBe(`Central question: ${ws.question}`)
    rerender(<TopBar {...props} source="live" workspace={ws} projectTitle="Project 1" />)
    expect(screen.getByTestId('project-heading').textContent).toBe('Project 1')
    rerender(<TopBar {...props} source="sample" workspace={ws} />) // demo: no project
    expect(screen.getByTestId('project-heading').textContent).toBe(ws.question)
  })
})

describe('About Us popover', () => {
  const props = { graph: null, workspace: null, query: '', onQuery: () => {}, onPick: () => {}, source: 'live',
    filters: { labels: {}, insufficient: true, structural: true, discovery: true, onlyNew: false }, onFilters: () => {} }
  it('opens from the contact button with each name linking to an email', () => {
    render(<TopBar {...props} health={{ gemini: true, database: true, brightdata: false }} onToggleAssistant={() => {}} />)
    const buttons = [...document.querySelectorAll('.tb-actions button')].map((b) => b.textContent.trim() || b.getAttribute('aria-label'))
    expect(buttons.slice(-2)).toEqual(['Assistant', 'About us'])
    expect(screen.queryByTestId('about')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'About us' }))
    const about = screen.getByTestId('about')
    expect(about.textContent).toMatch(/About Us/)
    expect(screen.getByText('Dhruv Madan').getAttribute('href')).toBe('mailto:dhruvmadan14@gmail.com')
    expect(screen.getByText('Samvit Kundapur').getAttribute('href')).toBe('mailto:skundapur@ucsd.edu')
    expect(screen.getByText('Aakarsh Jha').getAttribute('href')).toBe('mailto:aakarsh.jha08@gmail.com')
    expect(about.textContent).not.toMatch(/Services|Gemini|Bright Data/)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByTestId('about')).toBeNull()
  })
})