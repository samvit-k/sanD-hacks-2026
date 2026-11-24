// Full-screen and banner states of the app shell: data-source banners, the no-projects welcome and the
// connection / maintenance screen. Presentational only; all state comes from App.
import { useState } from 'react'

export function DataBanner({ source, error, onRetry, onExitDemo }) {
  if (source === 'sample') {
    return (
      <div className="banner banner-sample" role="status">
        <b>Demo mode — sample data (fictional)</b> — you chose to explore invented example papers and claims.
        Nothing here is real research evidence, and none of your projects are shown.
        <button type="button" className="link-btn" onClick={onExitDemo}>Exit demo</button>
      </div>
    )
  }
  if (source === 'snapshot') {
    return (
      <div className="banner" role="status">
        <b>Demo mode</b> — showing a saved snapshot of earlier pipeline output, not your live projects. Uploads are disabled.
        <button type="button" className="link-btn" onClick={onExitDemo}>Exit demo</button>
      </div>
    )
  }
  if (error) {
    return (
      <div className="banner banner-sample" role="alert">
        <b>Unable to load this project from the server.</b> {error.message} Nothing was changed.
        <button type="button" className="link-btn" onClick={onRetry}>Retry</button>
      </div>
    )
  }
  return null
}

export function NoProjects({ onCreate, switcher }) {
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      await onCreate()
    } catch (err) {
      setError(err?.message ?? 'The project could not be created.')
      setBusy(false)
    }
  }
  return (
    <div className="app app-empty">
      <aside className="sidebar" aria-label="Research workspace">
        <div className="sb-head">
          <img className="brand-mark" src="/logo.svg" alt="" aria-hidden="true" />
          <span className="sb-brand">SpiderMonkey</span>
        </div>
        <div className="sb-body">{switcher}</div>
      </aside>
      <main className="main">
        <section className="stage" aria-label="Start">
          <div className="onboard-card" role="region" aria-label="Create a research project">
            <div className="q-kicker">Welcome to SpiderMonkey</div>
            <p>Each research project has its own central question, papers, evidence graph and assistant conversation.</p>
            {error && <div className="warn-card" role="alert">{error}</div>}
            <button type="button" className="btn btn-sm btn-primary" onClick={create} disabled={busy}>
              {busy ? 'Creating…' : 'Create your first research project'}
            </button>
          </div>
        </section>
      </main>
    </div>
  )
}

/** The server could not be reached, or it needs maintenance. Never shows sample data in place of real research. */
export function ConnectionProblem({ maintenance, error, onRetry, onDemo }) {
  const [retrying, setRetrying] = useState(false)
  const retry = async () => {
    setRetrying(true)
    try {
      await onRetry()
    } finally {
      setRetrying(false)
    }
  }
  return (
    <div className="app app-empty">
      <aside className="sidebar" aria-label="Research workspace">
        <div className="sb-head">
          <img className="brand-mark" src="/logo.svg" alt="" aria-hidden="true" />
          <span className="sb-brand">SpiderMonkey</span>
        </div>
      </aside>
      <main className="main">
        <section className="stage" aria-label="Connection status">
          <div className="onboard-card conn-card" role="alert">
            {maintenance ? (
              <>
                <div className="q-kicker">Maintenance required</div>
                <h2>SpiderMonkey needs a one-time database upgrade</h2>
                <p>
                  The server is running, but its database still uses the single-project layout. Your research has not been
                  changed. An administrator must stop the backend, run <code>python -m app.migrate --backup-dir &lt;dir&gt;</code>
                  {' '}(it makes a verified backup first), and start the backend again.
                </p>
              </>
            ) : (
              <>
                <div className="q-kicker">Connection problem</div>
                <h2>Unable to connect to SpiderMonkey</h2>
                <p>
                  The server is not responding. Your projects are stored on the server and are not shown until it can be
                  reached — nothing is substituted for them.
                </p>
                {error?.message && <p className="conn-detail">Details: {error.message}</p>}
              </>
            )}
            <div className="conn-actions">
              <button type="button" className="btn btn-sm btn-primary" onClick={retry} disabled={retrying}>
                {retrying ? 'Retrying…' : 'Retry connection'}
              </button>
              <button type="button" className="link-btn muted" onClick={onDemo}>Explore fictional demo data instead</button>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}