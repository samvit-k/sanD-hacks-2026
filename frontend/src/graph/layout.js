// Deterministic radial hierarchy with gentle force spacing (question -> papers -> claims).
// Seeds are radial; d3-force (deterministic in v3) settles them organically, BioOKF-style.
import { forceCollide, forceLink, forceManyBody, forceRadial, forceSimulation, forceX, forceY } from 'd3-force'

export const RING = { paperMin: 190, paperSpan: 90, claimGap: 105, leadGap: 190 }
const TICKS = 300

function polar(r, a) {
  return { x: r * Math.cos(a), y: r * Math.sin(a) }
}

/**
 * @param graph  /api/graph payload
 * @param prev   optional {id: {x, y}} from the previous render; existing nodes start there
 * @returns {id: {x, y}}
 */
export function computeLayout(graph, prev = {}) {
  if (!graph?.nodes?.length) return {}
  const data = graph.nodes.map((n) => n.data)
  const papers = data
    .filter((d) => d.type === 'paper')
    .sort((a, b) => (a.label || '').localeCompare(b.label || '') || a.id.localeCompare(b.id))

  const rel = papers.map((p) => p.relevance).filter((v) => Number.isFinite(v))
  const lo = rel.length ? Math.min(...rel) : 0
  const hi = rel.length ? Math.max(...rel) : 0
  const target = {} // id -> {r, a}
  target.q = { r: 0, a: 0 }
  papers.forEach((p, i) => {
    const t = hi > lo && Number.isFinite(p.relevance) ? (p.relevance - lo) / (hi - lo) : 0.5 // 1 = most relevant
    target[p.id] = {
      r: RING.paperMin + (1 - t) * RING.paperSpan,
      a: -Math.PI / 2 + (2 * Math.PI * i) / Math.max(papers.length, 1),
    }
  })

  const claimsByPaper = {}
  for (const d of data) if (d.type === 'claim') (claimsByPaper[d.paper_node] ??= []).push(d)
  const sector = (2 * Math.PI) / Math.max(papers.length, 1)
  for (const [paperNode, claims] of Object.entries(claimsByPaper)) {
    const base = target[paperNode] ?? { r: RING.paperMin, a: 0 }
    claims.sort((a, b) => a.claim_id - b.claim_id)
    const spread = Math.min(1.1, sector * 0.7)
    claims.forEach((c, j) => {
      const off = claims.length > 1 ? (j / (claims.length - 1) - 0.5) * spread : 0
      target[c.id] = { r: base.r + RING.claimGap, a: base.a + off }
    })
  }

  const leads = data.filter((d) => d.type === 'discovery')
  leads.forEach((d, j) => {
    const anchor = target[d.anchor]
    const base = anchor && d.anchor !== 'q' ? anchor : { r: RING.paperMin + RING.paperSpan, a: 0 }
    const spread = anchor && d.anchor !== 'q' ? Math.min(1.2, sector * 0.8) : 2 * Math.PI * (1 - 1 / Math.max(leads.length, 1))
    const off = leads.length > 1 ? (j / (leads.length - 1) - 0.5) * spread : 0
    target[d.id] = { r: base.r + RING.leadGap, a: base.a + off }
  })

  // Question and papers are anchored on their rings (a readable hierarchy); claims and leads settle
  // organically around them. Each claim is weakly pulled toward its radial seed so dense evidence links
  // cannot collapse every paper's claims into one sector.
  const nodes = data.map((d) => {
    const t = target[d.id] ?? { r: RING.paperMin, a: 0 }
    const home = polar(t.r, t.a)
    const seed = prev[d.id] ?? home
    const node = { id: d.id, type: d.type, x: seed.x, y: seed.y, r: t.r, hx: home.x, hy: home.y }
    if (d.type === 'question') Object.assign(node, { fx: 0, fy: 0, x: 0, y: 0 })
    if (d.type === 'paper') Object.assign(node, { fx: home.x, fy: home.y, x: home.x, y: home.y })
    return node
  })
  const ids = new Set(nodes.map((n) => n.id))
  const links = []
  for (const { data: e } of graph.edges) {
    if (!ids.has(e.source) || !ids.has(e.target) || e.type === 'question_paper') continue
    if (e.type === 'has_claim') links.push({ source: e.source, target: e.target, distance: 78, strength: 0.9 })
    else if (e.type === 'discovered') links.push({ source: e.source, target: e.target, distance: 120, strength: 0.3 })
    else links.push({ source: e.source, target: e.target, distance: 200, strength: 0.01 })
  }

  const collide = (n) => (n.type === 'question' ? 70 : n.type === 'paper' ? 46 : 26)
  const sim = forceSimulation(nodes)
    .force('link', forceLink(links).id((n) => n.id).distance((l) => l.distance).strength((l) => l.strength))
    .force('radial', forceRadial((n) => n.r, 0, 0).strength((n) => (n.type === 'claim' ? 0.35 : 0.5)))
    .force('homeX', forceX((n) => n.hx).strength((n) => (n.type === 'claim' || n.type === 'discovery' ? 0.08 : 0)))
    .force('homeY', forceY((n) => n.hy).strength((n) => (n.type === 'claim' || n.type === 'discovery' ? 0.08 : 0)))
    .force('charge', forceManyBody().strength(-45).distanceMax(260))
    .force('collide', forceCollide(collide).strength(0.9))
    .stop()
  sim.alpha(Object.keys(prev).length ? 0.35 : 1)
  for (let i = 0; i < TICKS; i += 1) sim.tick()

  const out = {}
  for (const n of nodes) out[n.id] = { x: Math.round(n.x * 10) / 10, y: Math.round(n.y * 10) / 10 }
  return out
}
