// Floating vertical control stack (BioOKF bottom-right: + / − / fit / reset).
export default function ZoomControls({ zoom, onZoomIn, onZoomOut, onFit, onRelayout }) {
  return (
    <div className="zoom-controls" role="toolbar" aria-label="Graph view controls">
      <button type="button" onClick={onZoomIn} title="Zoom in" aria-label="Zoom in">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9" /></svg>
      </button>
      <button type="button" onClick={onZoomOut} title="Zoom out" aria-label="Zoom out">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9" /></svg>
      </button>
      <button type="button" onClick={onFit} title="Fit graph to view" aria-label="Fit graph to view">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 6.5V3h3.5M13 6.5V3H9.5M3 9.5V13h3.5M13 9.5V13H9.5" /></svg>
      </button>
      <button type="button" onClick={onRelayout} title="Reset layout" aria-label="Reset layout">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M12.5 8a4.5 4.5 0 1 1-1.3-3.2M12.8 2.8v2.6h-2.6" /></svg>
      </button>
      <div className="zoom-readout" aria-live="polite">{Math.round(zoom * 100)}%</div>
    </div>
  )
}