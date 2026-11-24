import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { computeLayout } from './layout.js'
import {
  DEFAULT_FILTERS, buildElements, hiddenIds, matchingIds, nodeIdForRef, nodeRefs, relationshipCounts, searchNodes,
} from './toElements.js'
import { BANNED_WORDS, IMPACTS, INSUFFICIENT, NODE_KINDS, RELATIONSHIPS, STRUCTURAL } from './vocab.js'

const fixture = (name) => JSON.parse(readFileSync(resolve(process.cwd(), 'public/fixtures', name), 'utf8'))
const graph = fixture('graph_with_new.json')
const dist = (p) => Math.hypot(p.x, p.y)

describe('buildElements', () => {
  const { nodes, edges } = buildElements(graph)

  it('maps every node to a known kind', () => {
    expect(nodes).toHaveLength(graph.nodes.length)
    for (const n of nodes) expect(NODE_KINDS[n.data.kind]).toBeDefined()
    expect(nodes.find((n) => n.data.id === 'q').data.kind).toBe('question')
    expect(nodes.find((n) => n.data.id === 'paper:sample-c').data.kind).toBe('paper_abstract')
    expect(nodes.find((n) => n.data.id === 'paper:sample-d').data.kind).toBe('paper_new')
    expect(nodes.find((n) => n.data.id === 'claim:4').data.kind).toBe('claim_new')
  })

  it('keeps structural edges visually separate from evidence edges', () => {
    for (const e of edges) {
      if (e.data.hitFor) {
        // Invisible click target for an evidence edge: same endpoints, never styled as evidence.
        expect(e.classes).toBe('evidence-hit')
        const twin = edges.find((x) => x.data.id === e.data.hitFor)
        expect([twin.data.source, twin.data.target]).toEqual([e.data.source, e.data.target])
      } else if (e.data.type === 'relationship') {
        expect(e.classes).toMatch(/^evidence rel-/)
        expect(RELATIONSHIPS[e.data.label]).toBeDefined()
      } else {
        expect(e.classes).toMatch(/^structural /)
        expect(e.classes).not.toMatch(/evidence|rel-/)
        expect(STRUCTURAL[e.data.type]).toBeDefined()
      }
    }
    expect(edges.find((e) => e.data.id === 'rel:5').classes).toContain('insufficient')
  })

  it('labels nodes compactly: Central Question, Node N, Lead N', () => {
    const label = (id) => nodes.find((n) => n.data.id === id).data.displayLabel
    expect(label('q')).toBe('Central Question')
    for (const n of nodes) {
      if (n.data.type === 'claim') expect(n.data.displayLabel).toMatch(/^Node \d+$/)
      if (n.data.type === 'paper') expect(n.data.displayLabel).toMatch(/^Node \d+( · new)?$/)
      if (n.data.type === 'discovery') expect(n.data.displayLabel).toMatch(/^Lead \d+$/)
    }
  })

  it('grows papers with their claim count', () => {
    const a = nodes.find((n) => n.data.id === 'paper:sample-a').data.size
    const d = nodes.find((n) => n.data.id === 'paper:sample-d').data.size
    expect(d).toBeGreaterThan(a) // 2 claims vs 1
  })
})

describe('filters and search', () => {
  it('default filters hide nothing', () => {
    expect(hiddenIds(graph, DEFAULT_FILTERS).size).toBe(0)
  })

  it('hides by label, insufficient, structure, leads and onlyNew', () => {
    const f = { ...DEFAULT_FILTERS, labels: { ...DEFAULT_FILTERS.labels, apparent_contradiction: false } }
    const hidden = hiddenIds(graph, f)
    expect(hidden.has('rel:1')).toBe(true)
    expect(hidden.has('rel:2')).toBe(false)
    expect(hiddenIds(graph, { ...DEFAULT_FILTERS, insufficient: false }).has('rel:5')).toBe(true)
    const noStruct = hiddenIds(graph, { ...DEFAULT_FILTERS, structural: false })
    expect(noStruct.has('has:1') && noStruct.has('about:sample-a')).toBe(true)
    expect(noStruct.has('rel:1')).toBe(false)
    const noLeads = hiddenIds(graph, { ...DEFAULT_FILTERS, discovery: false })
    expect(noLeads.has('disc:0') && noLeads.has('found:0')).toBe(true)
    const onlyNew = hiddenIds(graph, { ...DEFAULT_FILTERS, onlyNew: true })
    expect(onlyNew.has('rel:1') && onlyNew.has('rel:2')).toBe(true) // library-only pairs
    expect(onlyNew.has('rel:3')).toBe(false) // touches a new-paper claim
  })

  it('counts relationships per label', () => {
    const c = relationshipCounts(graph)
    expect(c.apparent_contradiction).toBe(2)
    expect(c.insufficient).toBe(1)
  })

  it('ranks starts-with before contains and searches full claim text', () => {
    const r = searchNodes(graph, 'ai tutor')
    expect(r.length).toBeGreaterThan(0)
    expect(r[0].text.toLowerCase().startsWith('ai tutor')).toBe(true)
    expect(searchNodes(graph, '')).toEqual([])
    expect(matchingIds(graph, 'six months').has('claim:4')).toBe(true)
    expect(matchingIds(graph, '  ').size).toBe(0)
  })
})

describe('computeLayout', () => {
  const pos = computeLayout(graph)

  it('positions every node with finite coordinates and pins the question at the centre', () => {
    for (const n of graph.nodes) {
      expect(Number.isFinite(pos[n.data.id].x) && Number.isFinite(pos[n.data.id].y)).toBe(true)
    }
    expect(pos.q).toEqual({ x: 0, y: 0 })
  })

  it('places claims further out than their paper (readable hierarchy)', () => {
    for (const { data } of graph.nodes.filter((n) => n.data.type === 'claim')) {
      expect(dist(pos[data.id])).toBeGreaterThan(dist(pos[data.paper_node]))
    }
  })

  it('puts more relevant papers closer to the question', () => {
    // sample-a relevance 0.91 vs sample-c 0.72
    expect(dist(pos['paper:sample-a'])).toBeLessThan(dist(pos['paper:sample-c']))
  })

  it('is deterministic', () => {
    expect(computeLayout(graph)).toEqual(pos)
  })

  it('starts existing nodes from previous positions when the graph grows', () => {
    const before = computeLayout(fixture('graph.json'))
    const after = computeLayout(graph, before)
    for (const id of Object.keys(before)) {
      const moved = Math.hypot(after[id].x - before[id].x, after[id].y - before[id].y)
      // Adding a paper re-spaces the paper ring (3 -> 4 sectors), so nodes glide a short way,
      // but never jump across the canvas (ring diameter here is ~560px).
      expect(moved).toBeLessThan(200)
    }
  })

  it('anchors papers on the ring in deterministic, evenly spaced sectors', () => {
    const papers = graph.nodes.filter((n) => n.data.type === 'paper').map((n) => pos[n.data.id])
    const angles = papers.map((p) => Math.atan2(p.y, p.x)).sort((a, b) => a - b)
    const gaps = angles.map((a, i) => (i ? a - angles[i - 1] : a + 2 * Math.PI - angles[angles.length - 1]))
    for (const g of gaps) expect(g).toBeCloseTo((2 * Math.PI) / papers.length, 5)
  })

  it('handles empty and question-only graphs', () => {
    expect(computeLayout(null)).toEqual({})
    const solo = { nodes: [{ data: { id: 'q', type: 'question', label: 'Q?' } }], edges: [] }
    expect(computeLayout(solo)).toEqual({ q: { x: 0, y: 0 } })
  })
})

describe('vocabulary', () => {
  it('never uses verdict words', () => {
    const strings = [RELATIONSHIPS, IMPACTS, NODE_KINDS, STRUCTURAL, { INSUFFICIENT }]
      .flatMap((o) => Object.values(o)).flatMap((v) => Object.values(v)).filter((v) => typeof v === 'string')
    for (const s of strings) {
      for (const w of BANNED_WORDS) expect(s.toLowerCase().split(/\W+/)).not.toContain(w)
    }
  })
})

describe('stable node numbers', () => {
  const withRefs = (g, refs) => ({ ...g, nodes: g.nodes.map(({ data }) => ({ data: { ...data, ref: refs[data.id] } })) })
  const live = withRefs(fixture('graph.json'), {})
  const liveRefs = nodeRefs(live)

  it('numbers every paper and claim uniquely, but not the question or leads', () => {
    const numbered = graph.nodes.filter((n) => ['paper', 'claim'].includes(n.data.type))
    const refs = nodeRefs(graph)
    expect(Object.keys(refs)).toHaveLength(numbered.length)
    expect(new Set(Object.values(refs)).size).toBe(numbered.length)
    expect(refs.q).toBeUndefined()
  })

  it('uses server refs verbatim and never renumbers when new nodes appear', () => {
    const server = {}
    let n = 0
    for (const { data } of fixture('graph.json').nodes) if (['paper', 'claim'].includes(data.type)) server[data.id] = ++n
    const before = nodeRefs(withRefs(fixture('graph.json'), server))
    expect(before).toEqual(server)
    // New paper D + claims inserted at the FRONT of the payload: earlier numbers still don't move.
    const bigger = withRefs(graph, server)
    bigger.nodes = [...bigger.nodes].reverse()
    const after = nodeRefs(bigger)
    for (const [id, num] of Object.entries(before)) expect(after[id]).toBe(num)
    const added = Object.keys(after).filter((id) => !(id in before)).map((id) => after[id])
    expect(Math.min(...added)).toBeGreaterThan(n)
  })

  it('resolves Node N back to the canonical id', () => {
    const [id, num] = Object.entries(liveRefs)[2]
    expect(nodeIdForRef(liveRefs, num)).toBe(id)
    expect(nodeIdForRef(liveRefs, 999)).toBeNull()
  })

  it('search still matches full text, and also "Node N"', () => {
    const anyClaim = Object.values(graph.claims)[0]
    const word = anyClaim.text.split(' ').slice(1, 3).join(' ')
    expect(searchNodes(graph, word).some((h) => h.id === `claim:${anyClaim.id}`)).toBe(true)
    const refs = nodeRefs(graph)
    const [id, num] = Object.entries(refs)[1]
    expect(searchNodes(graph, `node ${num}`)[0].id).toBe(id)
    expect([...matchingIds(graph, `Node ${num}`)]).toEqual([id])
  })
})