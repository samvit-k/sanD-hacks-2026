// Pure mapping from the /api/graph payload to Cytoscape elements, plus filtering and search.

const EVIDENCE = 'relationship'

export function shorten(text, n = 64) {
  if (!text) return ''
  return text.length <= n ? text : `${text.slice(0, n - 1).trimEnd()}…`
}

export function nodeKind(data, papers) {
  if (data.type === 'paper') {
    const p = papers?.[data.paper_id] ?? data
    if (p.is_new) return 'paper_new'
    if (p.access_level === 'abstract') return 'paper_abstract'
    return 'paper'
  }
  if (data.type === 'claim') return data.is_new ? 'claim_new' : 'claim'
  return data.type // question | discovery
}

export function isEvidenceEdge(data) {
  return data.type === EVIDENCE
}

export function claimCounts(graph) {
  const counts = {}
  for (const e of graph?.edges ?? []) {
    if (e.data.type === 'has_claim') counts[e.data.source] = (counts[e.data.source] ?? 0) + 1
  }
  return counts
}

export const CENTRAL_LABEL = 'Central Question'

/**
 * Stable display numbers ("Node N") for paper and claim nodes, keyed by graph node id.
 * The live API supplies `data.ref` from an append-only server table, so numbers never change
 * after refreshes or new uploads. Offline fixtures without refs get numbers continuing after
 * the highest known ref, in payload order.
 */
export function nodeRefs(graph) {
  const refs = {}
  const numbered = (graph?.nodes ?? []).filter(({ data }) => data.type === 'paper' || data.type === 'claim')
  let next = 0
  for (const { data } of numbered) {
    if (Number.isInteger(data.ref)) {
      refs[data.id] = data.ref
      next = Math.max(next, data.ref)
    }
  }
  for (const { data } of numbered) if (refs[data.id] == null) refs[data.id] = ++next
  return refs
}

/** Short canvas label for a node: "Central Question", "Node N", or "Lead N" for discovery leads. */
export function shortLabel(data, refs) {
  if (data.type === 'question') return CENTRAL_LABEL
  if (data.type === 'discovery') return `Lead ${(data.index ?? 0) + 1}`
  return refs[data.id] != null ? `Node ${refs[data.id]}` : 'Node'
}

/** Reverse lookup: display number -> graph node id (for chat references like "Node 7"). */
export function nodeIdForRef(refs, n) {
  return Object.keys(refs).find((id) => refs[id] === Number(n)) ?? null
}

/** API graph -> { nodes, edges } Cytoscape element definitions (no positions). */
export function buildElements(graph) {
  if (!graph) return { nodes: [], edges: [] }
  const counts = claimCounts(graph)
  const refs = nodeRefs(graph)
  const nodes = graph.nodes.map(({ data }) => {
    const kind = nodeKind(data, graph.papers)
    let size = 11
    let label = shortLabel(data, refs)
    if (kind === 'question') size = 30
    else if (kind.startsWith('paper')) size = 17 + Math.min(counts[data.id] ?? 0, 4) * 2
    else if (kind === 'discovery') size = 10
    if (kind === 'paper_new') label = `${label} · new`
    return {
      group: 'nodes',
      data: { ...data, kind, size, displayLabel: label },
      classes: [kind, kind.startsWith('claim') ? 'claim-node' : '', kind.startsWith('paper') ? 'paper-node' : '']
        .filter(Boolean).join(' '),
    }
  })
  const edges = graph.edges.map(({ data }) => {
    const evidence = isEvidenceEdge(data)
    const classes = evidence
      ? ['evidence', `rel-${data.label}`, data.insufficient_evidence ? 'insufficient' : '',
        data.independence === 'review' ? 'indep-review' : '', data.under_review ? 'under-review' : '']
        .filter(Boolean).join(' ')
      : `structural ${data.type}`
    return { group: 'edges', data: { ...data, evidence }, classes }
  })
  // Invisible, wider twins of evidence edges: Cytoscape's hit tolerance is fixed (width/2 + 8px),
  // so a transparent 14px stroke makes thin evidence lines easy to click without thickening them.
  for (const { data } of graph.edges) {
    if (!isEvidenceEdge(data)) continue
    edges.push({
      group: 'edges',
      data: { id: `hit:${data.id}`, source: data.source, target: data.target, type: data.type,
        relationship_id: data.relationship_id, hitFor: data.id, evidence: true },
      classes: 'evidence-hit',
    })
  }
  return { nodes, edges }
}

export const DEFAULT_FILTERS = {
  labels: {
    supports: true, genuine_contradiction: true, apparent_contradiction: true, unresolved: true, not_comparable: true,
  },
  insufficient: true,
  structural: true,
  discovery: true,
  onlyNew: false,
}

/** Ids of elements hidden by the filters (filters never re-run the layout). */
export function hiddenIds(graph, filters = DEFAULT_FILTERS) {
  const hidden = new Set()
  if (!graph) return hidden
  const newClaims = new Set(graph.nodes.filter((n) => n.data.type === 'claim' && n.data.is_new).map((n) => n.data.id))
  for (const { data } of graph.edges) {
    if (data.type === EVIDENCE) {
      const off =
        filters.labels?.[data.label] === false ||
        (data.insufficient_evidence && !filters.insufficient) ||
        (filters.onlyNew && !newClaims.has(data.source) && !newClaims.has(data.target))
      if (off) hidden.add(data.id)
    } else if (data.type === 'discovered') {
      if (!filters.discovery) hidden.add(data.id)
    } else if (!filters.structural) {
      hidden.add(data.id)
    }
  }
  if (!filters.discovery) {
    for (const { data } of graph.nodes) if (data.type === 'discovery') hidden.add(data.id)
  }
  return hidden
}

export function relationshipCounts(graph) {
  const counts = Object.fromEntries(Object.keys(DEFAULT_FILTERS.labels).map((k) => [k, 0]))
  let insufficient = 0
  for (const { data } of graph?.edges ?? []) {
    if (data.type !== EVIDENCE) continue
    counts[data.label] = (counts[data.label] ?? 0) + 1
    if (data.insufficient_evidence) insufficient += 1
  }
  return { ...counts, insufficient }
}

function searchText(data, graph) {
  if (data.type === 'claim') return graph.claims?.[String(data.claim_id)]?.text ?? data.label
  if (data.type === 'paper') return graph.papers?.[data.paper_id]?.title ?? data.label
  return data.label ?? ''
}

/** Ranked search results: "starts with" before "contains", capped (AGeneTic GeneSearch pattern). */
const NODE_REF_QUERY = /^node\s*#?(\d+)$/

/** Node id for a "Node 7" style query, if it names an existing node. */
function refHit(graph, q) {
  const m = NODE_REF_QUERY.exec(q)
  return m ? nodeIdForRef(nodeRefs(graph), m[1]) : null
}

export function searchNodes(graph, query, limit = 8) {
  const q = query.trim().toLowerCase()
  if (!q || !graph) return []
  const starts = []
  const contains = []
  const byRef = refHit(graph, q)
  for (const { data } of graph.nodes) {
    if (data.type === 'question') continue
    const text = searchText(data, graph)
    const t = text.toLowerCase()
    const hit = { id: data.id, type: data.type, text }
    if (data.id === byRef) starts.unshift(hit)
    else if (t.startsWith(q)) starts.push(hit)
    else if (t.includes(q)) contains.push(hit)
  }
  return [...starts, ...contains].slice(0, limit)
}

/** All node ids matching the query (for dimming non-matches). Empty set when no query. */
export function matchingIds(graph, query) {
  const q = query.trim().toLowerCase()
  if (!q || !graph) return new Set()
  const byRef = refHit(graph, q)
  if (byRef) return new Set([byRef])
  return new Set(
    graph.nodes.filter(({ data }) => data.type !== 'question' && searchText(data, graph).toLowerCase().includes(q))
      .map(({ data }) => data.id),
  )
}