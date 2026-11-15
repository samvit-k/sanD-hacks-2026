import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nodeRefs } from '../../graph/toElements.js'
import { BANNED_WORDS } from '../../graph/vocab.js'
import Inspector from './Inspector.jsx'

const graph = JSON.parse(readFileSync(resolve(process.cwd(), 'public/fixtures/graph_with_new.json'), 'utf8'))
const refs = nodeRefs(graph)

function show(selection, extra = {}) {
  const onNavigate = vi.fn()
  const onClose = vi.fn()
  render(<Inspector graph={graph} selection={selection} refs={refs} onNavigate={onNavigate} onClose={onClose} {...extra} />)
  return { onNavigate, onClose, root: screen.getByTestId('inspector') }
}

// Research content the app merely displays: verbatim quotes, model output, paper titles, claim text,
// the user's question. Banned-word rules apply to the app's own labels and verdict language only.
const CONTENT = '.vq, .model-box, .rel-paper, .insp-paperlink, .insp-q, .insp-title, .insp-row span:not(.rel-pill)'

function appLanguage(root) {
  const copy = root.cloneNode(true)
  copy.querySelectorAll(CONTENT).forEach((el) => el.remove())
  return copy.textContent
}

function expectNoBannedWords(root) {
  const text = appLanguage(root).toLowerCase()
  for (const w of BANNED_WORDS) expect(text).not.toMatch(new RegExp(`\\b${w}\\b`))
}

afterEach(cleanup)

describe('Inspector — evidence relationship', () => {
  it('shows both claims, verified quotes and the model assessment in separate, labelled sections', () => {
    const rel = graph.relationships['1']
    const { root } = show({ kind: 'relationship', id: 1 })
    expect(root.textContent).toContain(`Node ${refs['claim:1']} ↔ Node ${refs['claim:2']}`)
    expect(screen.getByText('Apparent contradiction')).toBeTruthy()
    // Model-generated text sits inside a box headed as a model assessment.
    const expl = screen.getByText(rel.explanation)
    expect(expl.closest('.model-box').textContent).toMatch(/Model assessment \(Gemini\) — not a verdict/)
    // Every cited quote appears as a verified source sentence, never inside a model box.
    for (const q of [...rel.cited.a, ...rel.cited.b]) {
      const quote = screen.getByText(q.text)
      expect(quote.closest('.vq')).toBeTruthy()
      expect(quote.closest('.model-box')).toBeNull()
      expect(quote.closest('.vq').textContent).toContain(q.id)
    }
    expect(screen.getByText(rel.claim_a.text)).toBeTruthy()
    expect(screen.getByText(rel.claim_b.text)).toBeTruthy()
    expect(root.textContent).toMatch(/Relevance to the central question/)
    expect(root.textContent).toContain(graph.question)
    expectNoBannedWords(root)
  })

  it('renders condition differences from condition_diff', () => {
    const rel = graph.relationships['1']
    show({ kind: 'relationship', id: 1 })
    const table = document.querySelector('.cond-table')
    for (const [, v] of Object.entries(rel.condition_diff)) {
      if (Array.isArray(v)) {
        for (const x of v) {
          if (x !== 'not reported') {
            expect(table.textContent).toContain(x)
          }
        }
      } else if (v === 'same') {
        expect(table.textContent).toContain('Same in both')
      }
    }
  })

  it('hides the judgment entirely when evidence is insufficient', () => {
    const rel = graph.relationships['5']
    const { root } = show({ kind: 'relationship', id: 5 })
    expect(screen.getAllByText(/Insufficient evidence/).length).toBeGreaterThan(0)
    expect(screen.queryByText(rel.explanation)).toBeNull()
    expect(root.textContent).not.toMatch(/Model assessment \(Gemini\)/)
    expect(root.textContent).not.toMatch(/Cited for this judgment/)
    expectNoBannedWords(root)
  })

  it('offers "Ask about this connection" only as a disabled control (no fake chat)', () => {
    show({ kind: 'relationship', id: 1 })
    expect(screen.getByRole('button', { name: 'Ask about this connection' }).disabled).toBe(true)
  })

  it('navigates to claims and closes', () => {
    const { onNavigate, onClose } = show({ kind: 'relationship', id: 1 })
    fireEvent.click(screen.getAllByRole('button', { name: `Node ${refs['claim:2']}` })[0])
    expect(onNavigate).toHaveBeenCalledWith({ kind: 'claim', id: 2 })
    fireEvent.click(screen.getByRole('button', { name: 'Close details' }))
    expect(onClose).toHaveBeenCalled()
  })
})

describe('Inspector — nodes', () => {
  it('claim view shows full text, type cue, quotes and its relationships', () => {
    const claim = graph.claims['1']
    const { root } = show({ kind: 'claim', id: 1 })
    expect(root.textContent).toContain(`Claim · Node ${refs['claim:1']}`)
    expect(screen.getByText(claim.text)).toBeTruthy()
    for (const q of claim.quotes) expect(screen.getByText(q.text).closest('.vq')).toBeTruthy()
    const rows = within(root).getAllByRole('button').filter((b) => b.className.includes('insp-row'))
    const expected = Object.values(graph.relationships).filter((r) => r.claim_a.id === 1 || r.claim_b.id === 1)
    expect(rows).toHaveLength(expected.length)
    expectNoBannedWords(root)
  })

  it('paper view shows the full title, node number and abstract-only badge', () => {
    const paper = graph.papers['sample-c']
    const { root } = show({ kind: 'paper', id: 'sample-c' })
    expect(screen.getByText(paper.title)).toBeTruthy()
    expect(root.textContent).toContain(`Node ${refs['paper:sample-c']}`)
    expect(screen.getByText('Abstract only')).toBeTruthy()
    expectNoBannedWords(root)
  })

  it('central question view shows the complete question', () => {
    const { root } = show({ kind: 'question', id: 'q' })
    expect(root.textContent).toContain('Central Question')
    expect(screen.getByText(graph.question)).toBeTruthy()
  })

  it('renders nothing for an unknown selection', () => {
    render(<Inspector graph={graph} selection={{ kind: 'claim', id: 999 }} refs={refs} onNavigate={() => {}} onClose={() => {}} />)
    expect(screen.queryByTestId('inspector')).toBeNull()
  })
})
