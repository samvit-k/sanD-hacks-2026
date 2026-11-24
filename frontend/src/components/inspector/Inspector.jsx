// Floating evidence inspector (BioOKF `.detail` card): hidden until something is selected,
// closes with ×, Esc (App) or a click on empty canvas.
import { DiscoveryView, PaperView, ClaimView, QuestionView } from './NodeViews.jsx'
import RelationshipView from './RelationshipView.jsx'

/** Resolve a selection against the graph payload; null when the item no longer exists. */
export function resolveSelection(graph, selection) {
  if (!graph || !selection) return null
  const { kind, id } = selection
  if (kind === 'question') return { kind, item: graph }
  if (kind === 'relationship') return graph.relationships?.[String(id)] ? { kind, item: graph.relationships[String(id)] } : null
  if (kind === 'claim') return graph.claims?.[String(id)] ? { kind, item: graph.claims[String(id)] } : null
  if (kind === 'paper') return graph.papers?.[id] ? { kind, item: graph.papers[id] } : null
  if (kind === 'discovery') {
    const lead = graph.discovery?.results?.[id]
    return lead ? { kind, item: lead } : null
  }
  return null
}

export default function Inspector({ graph, workspace, selection, refs, onNavigate, onClose, onAsk, onEditQuestion, impact = null }) {
  const resolved = resolveSelection(graph, selection)
  if (!resolved) return null
  const { kind, item } = resolved
  const props = { graph, refs, onNavigate, onAsk }
  return (
    <aside className={`inspector ${kind === 'relationship' ? 'wide' : ''}`} aria-label="Details" data-testid="inspector">
      <button type="button" className="icon-btn insp-close" onClick={onClose} aria-label="Close details">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
      </button>
      <div className="insp-body">
        {kind === 'relationship' && <RelationshipView rel={item} {...props} />}
        {kind === 'claim' && <ClaimView claim={item} {...props} impact={impact} />}
        {kind === 'paper' && <PaperView paper={item} {...props} />}
        {kind === 'question' && <QuestionView graph={graph} workspace={workspace} onAsk={onAsk} onEditQuestion={onEditQuestion} />}
        {kind === 'discovery' && <DiscoveryView lead={item} discovery={graph.discovery} />}
      </div>
    </aside>
  )
}