import { useCallback, useEffect, useState } from 'react'
import { loadFixture } from '../api/client.js'

// Loads everything the page shows from the live API. If the server is unreachable, it falls back to
// saved fixtures and labels them. Fixtures are treated as fictional SAMPLE data unless the file is
// explicitly marked as a real pipeline snapshot (meta.source === "snapshot", written by
// backend/scripts/snapshot.py). Fictional data is never presented as live research results.

export function fixtureSource(graph) {
  return graph?.meta?.source === 'snapshot' ? 'snapshot' : 'sample'
}

export function workspaceFromGraph(graph, source) {
  const papers = Object.values(graph.papers ?? {})
  const library = papers.filter((p) => !p.is_new)
  return {
    question: graph.question,
    source,
    counts: {
      papers: papers.length,
      library_papers: library.length,
      evaluated_papers: papers.length - library.length,
      claims: Object.keys(graph.claims ?? {}).length,
      relationships: Object.keys(graph.relationships ?? {}).length,
    },
    has_library: library.length > 0,
  }
}

/**
 * Data for one research project.
 * - `client` (sessionApi): live data only. If the server cannot be reached the hook reports an error — it NEVER
 *   substitutes fixture/sample data for a real project (that would make the evidence appear to have changed).
 * - `demo: true` (and no client): the fictional sample dataset, only when the user explicitly chose demo mode.
 *   It is always labelled by its provenance (meta.source: sample | snapshot).
 * `refresh({ fallback: false })` (used after uploads) keeps the current view and throws on failure, so the
 * caller can say "refresh failed" and retry just the refresh.
 */
export function useResearch(client, { demo = false } = {}) {
  const [state, setState] = useState({
    status: 'loading', source: null, graph: null, workspace: null, impact: null, checks: null, health: null, error: null,
  })

  const refresh = useCallback(async ({ fallback = true } = {}) => {
    if (!client) {
      if (!demo) {
        const error = new Error('No research project is open.')
        setState((s) => ({ ...s, status: 'error', error }))
        return null
      }
      try {
        const graph = await loadFixture('graph_with_new.json')
        const source = fixtureSource(graph)
        const impact = await loadFixture('paper_d_result.json').catch(() => null)
        setState({ status: 'ready', source, graph, workspace: workspaceFromGraph(graph, source), impact, checks: null,
          health: null, error: null })
      } catch (error) {
        setState((s) => ({ ...s, status: 'error', error }))
      }
      return null
    }
    try {
      // graph + workspace are required; impact, checks and health are optional.
      const [graph, workspace, impact, checks, health] = await Promise.all([
        client.graph(),
        client.workspace(),
        client.latestImpact().catch(() => null),
        client.checks().catch(() => null),
        client.health().catch(() => null),
      ])
      setState({ status: 'ready', source: 'live', graph, workspace, impact, checks, health, error: null })
      return { graph, workspace, impact }
    } catch (error) {
      if (!fallback) throw error
      // Keep whatever real data is already shown; never replace it with sample data.
      setState((s) => ({ ...s, status: 'error', error }))
      return null
    }
  }, [client, demo])

  useEffect(() => {
    refresh()
  }, [refresh])

  const setImpact = useCallback((impact) => setState((s) => ({ ...s, impact })), [])
  return { ...state, refresh, setImpact }
}