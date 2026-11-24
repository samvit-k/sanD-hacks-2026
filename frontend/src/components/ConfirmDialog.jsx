// Confirmation for a permanent, project-scoped action (clear chat, clear papers, remove a paper).
// Same dialog language as deleting a project: the project is named, the consequences are stated exactly,
// and the action runs only after an explicit click. Errors are shown in place; nothing is retried silently.
import { useEffect, useRef, useState } from 'react'

export default function ConfirmDialog({ title, project = null, children, confirmLabel, busyLabel = 'Working…', onConfirm, onCancel }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const cancelRef = useRef(null)
  useEffect(() => { cancelRef.current?.focus() }, [])
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onCancel])
  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm()
    } catch (err) {
      setError(err?.message ?? 'Nothing was changed.')
      setBusy(false)
    }
  }
  return (
    <div className="qd-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel() }}>
      <div className="qd qd-small" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <div className="qd-head"><h2 id="confirm-title">{title}</h2></div>
        <div className="qd-body">
          {project && <p className="qd-current"><span>Project:</span> {project}</p>}
          <div className="qd-danger">{children}</div>
          {error && <div className="warn-card" role="alert">{error}</div>}
        </div>
        <div className="qd-foot">
          <button type="button" className="btn btn-sm" ref={cancelRef} onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={run}>
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}