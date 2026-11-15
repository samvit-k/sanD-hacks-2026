import FilterPopover from './FilterPopover.jsx'
import SearchBox from './SearchBox.jsx'

const CHECKS_HELP =
  'Citation integrity: every claim, relationship and impact cites existing source sentences from the right papers, ' +
  'so each statement is traceable to the original text. It does not assess whether the scientific claims are correct.'

/** Interpret GET /api/checks defensively. Anything malformed is "unavailable", never "passed". */
export function checksStatus(checks) {
  const v = checks?.violations
  const valid = checks && typeof checks.ok === 'boolean' && v && typeof v === 'object' && !Array.isArray(v) &&
    Object.keys(v).length > 0 && Object.values(v).every(Array.isArray)
  if (!valid) return { state: 'unavailable', issues: 0 }
  const issues = Object.values(v).reduce((n, ids) => n + ids.length, 0)
  if (issues > 0) return { state: 'issues', issues }
  return checks.ok ? { state: 'passed', issues: 0 } : { state: 'unavailable', issues: 0 } // inconsistent -> not a pass
}

function ChecksPill({ checks, source }) {
  if (source !== 'live') return null
  const s = checksStatus(checks)
  if (s.state === 'unavailable') return <span className="pill" title={CHECKS_HELP}>Checks Unavailable</span>
  if (s.state === 'issues') {
    return <span className="pill pill-bad" title={CHECKS_HELP}><b className="bad">{s.issues}</b> Citation Integrity Issues</span>
  }
  return <span className="pill" title={CHECKS_HELP}><b className="good">✓</b> Citation Integrity Passed</span>
}

function ServiceDots({ health }) {
  if (!health) return null
  const items = [['Gemini', health.gemini], ['Database', health.database], ['Bright Data', health.brightdata]]
  const title = items.map(([name, on]) => `${name}: ${on ? 'configured' : 'not configured'}`).join('\n')
  return (
    <span className="services" title={title} aria-label={title.replace(/\n/g, ', ')}>
      {items.map(([name, on]) => <span key={name} className={`svc-dot ${on ? 'on' : ''}`} />)}
    </span>
  )
}

export default function TopBar({
  graph, workspace, checks, health, source, query, onQuery, onPick, filters, onFilters, assistantOpen, onToggleAssistant,
  projectTitle = null,
}) {
  // The project's (renameable) title; the full central question stays in the tooltip and the left panel.
  const heading = projectTitle || workspace?.question || 'No Research Question Yet'
  const tooltip = workspace?.question ? `Central question: ${workspace.question}` : heading
  const c = workspace?.counts ?? {}
  return (
    <header className="topbar">
      <div className="tb-title">
        <span className="tb-name" title={tooltip} data-testid="project-heading">{heading}</span>
        <span className="tb-meta">
          {c.papers ?? 0} Papers · {c.claims ?? 0} Claims · {c.relationships ?? 0} Relationships
        </span>
      </div>
      <div className="tb-actions">
        <ChecksPill checks={checks} source={source} />
        <SearchBox graph={graph} query={query} onQuery={onQuery} onPick={onPick} />
        <FilterPopover graph={graph} filters={filters} onChange={onFilters} />
        <ServiceDots health={health} />
        {onToggleAssistant && (
          <button type="button" className={`btn ${assistantOpen ? 'btn-on' : ''}`} onClick={onToggleAssistant}
            aria-pressed={!!assistantOpen} aria-controls="assistant-panel" title="Research assistant and PDF uploads">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" /></svg>
            Assistant
          </button>
        )}
      </div>
    </header>
  )
}
