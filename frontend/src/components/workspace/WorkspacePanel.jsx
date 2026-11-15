import { describeExtraction } from '../../graph/extraction.js'
import { claimCounts } from '../../graph/toElements.js'

// Left research workspace (BioOKF sidebar language): the central question (set / change through the
// question dialog), deliberate re-analysis after a confirmed question change, and the papers.
function PaperList({ graph, selection, refs, onSelectPaper }) {
  const papers = Object.values(graph?.papers ?? {})
  const counts = claimCounts(graph)
  if (!papers.length) return <p className="empty-note">No papers yet.</p>
  const ordered = [...papers.filter((p) => !p.is_new), ...papers.filter((p) => p.is_new)]
  return (
    <ul className="paper-list">
      {ordered.map((p) => {
        const active = selection?.kind === 'paper' && selection.id === p.id
        const n = counts[`paper:${p.id}`] ?? 0
        const outcome = describeExtraction(p.extraction)
        return (
          <li key={p.id}>
            <button type="button" className={`paper-card ${active ? 'active' : ''}`} onClick={() => onSelectPaper(p.id)}>
              <span className="paper-title">
                <span className={`node-dot ${p.access_level === 'abstract' ? 'dashed' : ''}`}
                  style={{ background: p.access_level === 'abstract' ? '#8A86C4' : '#6366A8', boxShadow: p.is_new ? '0 0 0 2px #0ea5e9' : undefined }} />
                <span className="paper-title-text">{p.title}</span>
                {refs?.[`paper:${p.id}`] != null && <span className="node-chip paper">Node {refs[`paper:${p.id}`]}</span>}
              </span>
              <span className="paper-meta">
                {n} {n === 1 ? 'claim' : 'claims'} · {p.access_level === 'abstract' ? 'abstract only' : 'full text'}
                {p.year ? ` · ${p.year}` : ''}
              </span>
              {(p.is_new || p.access_level === 'abstract' || p.analysis_status === 'pending' || outcome) && (
                <span className="badges">
                  {outcome && p.analysis_status !== 'pending' && (
                    <span className="badge badge-pending" title={outcome.title}>{outcome.badge}</span>
                  )}
                  {p.analysis_status === 'pending' && <span className="badge badge-pending">Awaiting analysis</span>}
                  {p.is_new && <span className="badge badge-new">Evaluated</span>}
                  {p.access_level === 'abstract' && <span className="badge">Abstract only</span>}
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function ReanalysisCard({ pending, reanalysis, canRun }) {
  const n = pending.length
  if (!n && !reanalysis.running) return null
  return (
    <div className="reanalysis-card" role="status" data-testid="reanalysis">
      {reanalysis.running ? (
        <>
          <div className="ra-title"><span className="spinner" aria-hidden="true" /> Re-analyzing {reanalysis.done + 1} of {reanalysis.total}</div>
          <div className="ra-text">{reanalysis.current?.title} — extracting claims, comparing{reanalysis.current?.is_new ? ', assessing impact' : ''}.</div>
        </>
      ) : (
        <>
          <div className="ra-title">{n} {n === 1 ? 'paper awaits' : 'papers await'} analysis under the new question</div>
          <div className="ra-text">
            Their text is stored. Claims, relationships and impacts are extracted and judged again only when you start it
            (Gemini calls, usually 20–80 s per paper). Uploads are paused until then.
          </div>
          {reanalysis.error && (
            <div className="ra-error" role="alert">“{reanalysis.error.paper.title}” was not analyzed: {reanalysis.error.message}</div>
          )}
          <button type="button" className="btn btn-sm" disabled={!canRun} onClick={() => reanalysis.start(pending)}>
            {reanalysis.error || reanalysis.done ? 'Continue re-analysis' : `Re-analyze ${n} ${n === 1 ? 'paper' : 'papers'}`}
          </button>
        </>
      )}
    </div>
  )
}

export default function WorkspacePanel({
  graph, workspace, selection, refs, onSelectPaper, onSelectQuestion, onEditQuestion, canEditQuestion, reanalysis,
  collapsed, onToggle, switcher = null,
}) {
  const pending = workspace?.pending_papers ?? []
  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`} aria-label="Research workspace">
      <div className="sb-head">
        <img className="brand-mark" src="/logo.svg" alt="" aria-hidden="true" />
        <span className="sb-brand">SpiderMonkey</span>
        <button type="button" className="icon-btn" onClick={onToggle} aria-label={collapsed ? 'Expand workspace' : 'Collapse workspace'}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d={collapsed ? 'M6 3.5 10.5 8 6 12.5' : 'M10 3.5 5.5 8 10 12.5'} /></svg>
        </button>
      </div>
      {!collapsed && (
        <div className="sb-body">
          {switcher}
          <div className="sb-section">Research question</div>
          <div className="question-wrap">
            <button type="button" className={`question-card ${selection?.kind === 'question' ? 'active' : ''}`}
              onClick={onSelectQuestion} title="Show the Central Question in the graph">
              <div className="q-kicker">Central Question</div>
              <div className="q-text">{workspace?.question || 'No research question set'}</div>
              <div className="q-meta">
                {workspace?.counts?.library_papers ?? 0} library papers · {workspace?.counts?.claims ?? 0} claims
              </div>
              {workspace?.source === 'default' && <div className="q-hint">Demo default from .env</div>}
            </button>
            {onEditQuestion && (
              <button type="button" className={workspace?.question ? 'link-btn q-edit' : 'btn btn-sm q-set'}
                disabled={!canEditQuestion} onClick={onEditQuestion}
                title={canEditQuestion ? undefined : 'Not available while papers are being analyzed or offline.'}>
                {workspace?.question ? 'Change Question' : 'Set the central question'}
              </button>
            )}
          </div>
          {reanalysis && <ReanalysisCard pending={pending} reanalysis={reanalysis} canRun={!!canEditQuestion} />}

          <div className="sb-section">Papers</div>
          <PaperList graph={graph} selection={selection} refs={refs} onSelectPaper={onSelectPaper} />
        </div>
      )}
    </aside>
  )
}
