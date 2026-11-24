// Viewport fitting that respects what covers the canvas (the legend card) and what the user did.
//
// Policy (D5.4):
//  - Until the user pans or zooms by hand, the view follows the available space: when the canvas is resized
//    (Assistant panel opened/closed, sidebar, window) or the legend opens/closes, the whole graph is refitted.
//  - Once the user has panned/zoomed (or focused an item), resizes never refit; the point at the centre of the
//    view stays at the centre instead. The Fit control refits and resumes following.
//  - Fitting leaves room for the legend: the graph is fitted into the larger free rectangle beside or above it.
//  - Fitting limit: a fit shows the whole graph whenever that needs a zoom >= the instance's minZoom
//    (GRAPH_MIN_ZOOM = 0.02, i.e. a graph up to ~50x the canvas size). Beyond that the zoom is clamped to the
//    minimum, the graph is centred, and the result says `clipped: true` — it is never claimed to fit.

export const FIT_PADDING = 48
export const MAX_FIT_ZOOM = 1.25
export const GRAPH_MIN_ZOOM = 0.02 // Cytoscape minZoom (GraphCanvas): low enough for any realistic graph
export const GRAPH_MAX_ZOOM = 3
const LEGEND_MARGIN = 12
// Cytoscape emits these only for user gestures; programmatic pan()/zoom()/animate()/fit() never do.
export const USER_VIEWPORT_EVENTS = 'dragpan scrollzoom pinchzoom'
// Window event the Legend dispatches after it opens/closes, so the graph can refit around it.
export const LEGEND_EVENT = 'spidermonkey:legend'

/** Insets ({left, top, right, bottom}) that keep fitted content clear of a bottom-left legend card.
 *  Chooses between reserving a left strip or a bottom strip, whichever leaves the larger area. */
export function legendInsets(size, legend) {
  const none = { left: 0, top: 0, right: 0, bottom: 0 }
  if (!legend || legend.width <= 0 || legend.height <= 0) return none
  const left = Math.min(size.width, legend.right + LEGEND_MARGIN)
  const bottom = Math.min(size.height, size.height - legend.top + LEGEND_MARGIN)
  const areaBeside = Math.max(0, size.width - left) * size.height
  const areaAbove = size.width * Math.max(0, size.height - bottom)
  return areaBeside >= areaAbove ? { ...none, left } : { ...none, bottom }
}

/** Zoom and pan that fit a model bounding box into the canvas minus insets and padding.
 *  `maxZoom` caps zooming IN; `minZoom` is the instance's hard limit. Returns {zoom, pan, clipped}. */
export function fitViewport(bb, size, insets = {}, { padding = FIT_PADDING, maxZoom = MAX_FIT_ZOOM, minZoom = GRAPH_MIN_ZOOM } = {}) {
  const ins = { left: 0, top: 0, right: 0, bottom: 0, ...insets }
  const availW = Math.max(1, size.width - ins.left - ins.right - 2 * padding)
  const availH = Math.max(1, size.height - ins.top - ins.bottom - 2 * padding)
  const needed = Math.min(availW / Math.max(bb.w, 1), availH / Math.max(bb.h, 1))
  const zoom = Math.max(minZoom, Math.min(maxZoom, needed))
  const cx = (bb.x1 + bb.x2) / 2
  const cy = (bb.y1 + bb.y2) / 2
  const centerX = ins.left + (size.width - ins.left - ins.right) / 2
  const centerY = ins.top + (size.height - ins.top - ins.bottom) / 2
  return { zoom, pan: { x: centerX - cx * zoom, y: centerY - cy * zoom }, clipped: needed < minZoom }
}

/** Pan that keeps the model point at the centre of the old view at the centre of the new view. */
export function keepCenterPan(pan, oldSize, newSize) {
  return { x: pan.x + (newSize.width - oldSize.width) / 2, y: pan.y + (newSize.height - oldSize.height) / 2 }
}

/**
 * Controller for one Cytoscape instance. Uses cy.width/height/pan/zoom/minZoom/maxZoom/elements/resize/animate/
 * stop/on/off. `getLegend()` returns the legend card rectangle relative to the canvas, or null.
 * Returns { fit, userAdjusted, resized, legendChanged, following, lastFit, dispose }.
 */
export function createAutoFit(cy, getLegend = () => null, { animate = true } = {}) {
  let following = true // follows the available space until the user adjusts the view
  let resizing = false // re-entrancy guard: cy.resize() must never re-run this handler
  let lastFit = null
  const measure = () => ({ width: cy.width(), height: cy.height() })
  let size = measure()

  const limits = () => ({ minZoom: cy.minZoom?.() ?? GRAPH_MIN_ZOOM, maxZoom: Math.min(MAX_FIT_ZOOM, cy.maxZoom?.() ?? MAX_FIT_ZOOM) })
  const target = () => {
    const eles = cy.elements(':visible')
    if (!eles.length) return null
    const current = measure()
    return fitViewport(eles.boundingBox(), current, legendInsets(current, getLegend()), limits())
  }
  const stopAnimations = () => cy.stop?.(true, false) // drop queued/running viewport animations
  const apply = (view, duration) => {
    if (!view) return
    lastFit = view
    stopAnimations()
    if (animate && duration) cy.animate({ zoom: view.zoom, pan: view.pan }, { duration, easing: 'ease-in-out-cubic' })
    else { cy.zoom(view.zoom); cy.pan(view.pan) }
  }
  const onUser = () => { following = false }
  cy.on(USER_VIEWPORT_EVENTS, onUser)

  return {
    get following() { return following },
    /** The last fit applied ({zoom, pan, clipped}); `clipped` means the graph needed less than minZoom. */
    get lastFit() { return lastFit },
    /** Fit now and resume following (initial view, Fit control). */
    fit(duration = 0) {
      following = true
      apply(target(), duration)
    },
    /** A zoom button or a focus on one item (hand gestures are detected automatically): stop following. */
    userAdjusted() { following = false },
    /** The container may have changed size. Cytoscape caches its dimensions, so refresh them FIRST, then compare. */
    resized(duration = 0) {
      if (resizing) return
      resizing = true
      try {
        cy.resize()
        const next = measure()
        if (next.width === size.width && next.height === size.height) return
        const prev = size
        size = next
        if (following) apply(target(), duration)
        else {
          stopAnimations()
          cy.pan(keepCenterPan(cy.pan(), prev, next))
        }
      } finally {
        resizing = false
      }
    },
    /** Something covering or filling the canvas changed (legend opened/closed, graph updated): refit only while
     *  following. */
    refitIfFollowing(duration = 0) {
      if (following) apply(target(), duration)
    },
    legendChanged(duration = 0) {
      if (following) apply(target(), duration)
    },
    dispose() { cy.off(USER_VIEWPORT_EVENTS, onUser) },
  }
}