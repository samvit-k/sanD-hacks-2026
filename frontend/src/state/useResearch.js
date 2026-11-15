import { useCallback, useEffect, useState } from 'react'
import { api, loadFixture } from '../api/client.js'

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

export function useResearch() {
  const [state, setState] = useState({
    status: 'loading', source: null, graph: null, workspace: null, impact: null, checks: null, health: null, error: null,
  })

  // `fallback: false` (used after uploads) never swaps live data for fixtures: on failure it keeps the
  // current view and throws, so the caller can say "refresh failed" and retry just the refresh.
  const refresh = useCallback(async ({ fallback = true } = {}) => {
    try {
      // graph + workspace are required; impact, checks and health are optional and never trigger the fallback.
      const [graph, workspace, impact, checks, health] = await Promise.all([
        api.graph(),
        api.workspace(),
        api.latestImpact().catch(() => null),
        api.checks().catch(() => null),
        api.health().catch(() => null),
      ])
      setState({ status: 'ready', source: 'live', graph, workspace, impact, checks, health, error: null })
      return { graph, workspace, impact }
    } catch (error) {
      if (!fallback) throw error
      try {
        const graph = await loadFixture('graph_with_new.json')
        const source = fixtureSource(graph)
        const impact = await loadFixture('paper_d_result.json').catch(() => null)
        setState({
          status: 'ready', source, graph, workspace: workspaceFromGraph(graph, source), impact, checks: null, health: null, error,
        })
        return null
      } catch {
        setState((s) => ({ ...s, status: 'error', error }))
        return null
      }
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const setImpact = useCallback((impact) => setState((s) => ({ ...s, impact })), [])
  return { ...state, refresh, setImpact }
}
