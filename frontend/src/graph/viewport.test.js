import { describe, expect, it } from 'vitest'
import { createAutoFit, fitViewport, GRAPH_MIN_ZOOM, keepCenterPan, legendInsets, USER_VIEWPORT_EVENTS } from './viewport.js'

const BB = { x1: -200, x2: 200, y1: -150, y2: 150, w: 400, h: 300 }
// A tall legend card: area beside it (1200-468)*800 = 585,600 > area above it 1200*(800-332) = 561,600.
const TALL_LEGEND = { left: 16, right: 456, top: 480, bottom: 784, width: 440, height: 304 }
// A short, wide legend card: area beside it (1200-1012)*800 = 150,400 < area above it 1200*(800-112) = 825,600.
const WIDE_LEGEND = { left: 16, right: 1000, top: 700, bottom: 784, width: 984, height: 84 }

const box = (bb, view) => ({
  left: bb.x1 * view.zoom + view.pan.x, right: bb.x2 * view.zoom + view.pan.x,
  top: bb.y1 * view.zoom + view.pan.y, bottom: bb.y2 * view.zoom + view.pan.y,
})

describe('fitViewport', () => {
  it('fits the bounding box inside the canvas minus insets, centred in the free area', () => {
    const view = fitViewport(BB, { width: 1000, height: 800 }, { left: 300 }, { padding: 50, maxZoom: 5 })
    // free area 700 x 800, minus padding -> 600 x 700; zoom = min(600/400, 700/300) = 1.5
    expect(view.zoom).toBeCloseTo(1.5)
    const b = box(BB, view)
    expect(b.left).toBeGreaterThanOrEqual(300 + 50 - 0.01) // nothing under the legend strip
    expect(b.right).toBeLessThanOrEqual(1000 - 50 + 0.01)
    expect((b.left + b.right) / 2).toBeCloseTo(300 + 700 / 2)
    expect(view.clipped).toBe(false)
  })

  it('caps zooming in on small graphs', () => {
    expect(fitViewport({ x1: 0, x2: 10, y1: 0, y2: 10, w: 10, h: 10 }, { width: 1000, height: 800 }).zoom).toBe(1.25)
  })

  it('fits a large graph on a narrow canvas completely, zooming out as far as needed', () => {
    const big = { x1: -4000, x2: 4000, y1: -3000, y2: 3000, w: 8000, h: 6000 }
    const size = { width: 360, height: 640 }
    const padding = 48
    const view = fitViewport(big, size, {}, { padding })
    expect(view.zoom).toBeLessThan(0.2) // below the old hard floor, which clipped this graph
    expect(view.clipped).toBe(false)
    const b = box(big, view)
    expect(b.left).toBeGreaterThanOrEqual(padding - 0.01)
    expect(b.right).toBeLessThanOrEqual(size.width - padding + 0.01)
    expect(b.top).toBeGreaterThanOrEqual(padding - 0.01)
    expect(b.bottom).toBeLessThanOrEqual(size.height - padding + 0.01)
  })

  it('a graph needing less than the minimum zoom is clamped, centred and reported as clipped', () => {
    const huge = { x1: -1e6, x2: 1e6, y1: -1e6, y2: 1e6, w: 2e6, h: 2e6 }
    const view = fitViewport(huge, { width: 400, height: 400 })
    expect(view.zoom).toBe(GRAPH_MIN_ZOOM)
    expect(view.clipped).toBe(true) // never claimed to fit
    const b = box(huge, view)
    expect((b.left + b.right) / 2).toBeCloseTo(200)
    expect((b.top + b.bottom) / 2).toBeCloseTo(200)
  })
})

describe('legendInsets', () => {
  const size = { width: 1200, height: 800 }
  it('reserves the strip beside the legend when that leaves the larger area', () => {
    expect(legendInsets(size, TALL_LEGEND)).toEqual({ left: 468, top: 0, right: 0, bottom: 0 })
    expect(legendInsets({ width: 1600, height: 500 }, { left: 16, right: 256, top: 60, bottom: 484, width: 240, height: 424 }))
      .toEqual({ left: 268, top: 0, right: 0, bottom: 0 })
  })

  it('reserves the strip above the legend when that leaves the larger area', () => {
    expect(legendInsets(size, WIDE_LEGEND)).toEqual({ left: 0, top: 0, right: 0, bottom: 112 })
  })

  it('reserves nothing without a legend', () => {
    expect(legendInsets(size, null)).toEqual({ left: 0, top: 0, right: 0, bottom: 0 })
  })
})

describe('keepCenterPan', () => {
  it('keeps the centre model point centred when the canvas narrows', () => {
    const pan = { x: 100, y: 50 }
    const zoom = 2
    const old = { width: 1000, height: 800 }
    const next = { width: 600, height: 800 }
    const np = keepCenterPan(pan, old, next)
    expect((next.width / 2 - np.x) / zoom).toBeCloseTo((old.width / 2 - pan.x) / zoom)
  })
})

/** Fake Cytoscape: like the real one, width()/height() are cached until resize(); only user gestures emit the
 *  USER_VIEWPORT_EVENTS; animate() is queued until flush(); stop() drops queued/running animations. */
function fakeCy(width, height, bb = BB) {
  const state = { container: { width, height }, cached: { width, height }, zoom: 1, pan: { x: 0, y: 0 },
    resizes: 0, stops: 0, queue: [], onResize: null }
  const handlers = {}
  const eles = { length: 3, boundingBox: () => bb }
  return {
    state,
    width: () => state.cached.width,
    height: () => state.cached.height,
    minZoom: () => GRAPH_MIN_ZOOM,
    maxZoom: () => 3,
    zoom: (z) => (z === undefined ? state.zoom : (state.zoom = z)),
    pan: (p) => (p === undefined ? { ...state.pan } : (state.pan = { ...p })),
    elements: () => eles,
    resize: () => { state.resizes += 1; state.cached = { ...state.container }; state.onResize?.() },
    animate: (opts) => { state.queue.push(opts) },
    stop: () => { state.stops += 1; state.queue = [] },
    on: (events, fn) => events.split(' ').forEach((e) => { handlers[e] = fn }),
    off: (events) => events.split(' ').forEach((e) => { delete handlers[e] }),
    emit: (e) => handlers[e]?.(),
    flush: () => { const a = state.queue.pop(); state.queue = []; if (a) { state.zoom = a.zoom; state.pan = a.pan } },
    setContainer: (w, h) => { state.container = { width: w, height: h } }, // CSS layout changed; not yet measured
  }
}

describe('createAutoFit', () => {
  it('refits when the Assistant panel narrows the canvas, until the user adjusts the view', () => {
    const cy = fakeCy(1400, 900)
    const fit = createAutoFit(cy, () => null, { animate: false })
    fit.fit()
    const wide = cy.state.zoom
    cy.setContainer(900, 900) // Assistant panel opened
    fit.resized()
    expect(cy.state.resizes).toBe(1)
    expect(box(BB, cy.state).right).toBeLessThanOrEqual(900) // every node still inside the narrower canvas
    expect(cy.state.zoom).toBeLessThanOrEqual(wide)

    cy.emit('scrollzoom')       // the user zooms by hand
    expect(fit.following).toBe(false)
    cy.state.zoom = 2
    cy.state.pan = { x: 333, y: 111 }
    cy.setContainer(1400, 900)  // panel closed
    fit.resized()
    expect(cy.state.zoom).toBe(2)                          // no disruptive refit
    expect(cy.state.pan).toEqual({ x: 333 + 250, y: 111 }) // the centre point stays centred

    fit.fit()                   // the Fit control resumes following
    expect(fit.following).toBe(true)
  })

  it('refreshes the cached dimensions before measuring (a resize is never missed)', () => {
    const cy = fakeCy(1400, 900)
    const fit = createAutoFit(cy, () => null, { animate: false })
    fit.fit()
    cy.setContainer(900, 900)
    expect(cy.width()).toBe(1400) // still stale, as in Cytoscape
    fit.resized()
    expect(cy.width()).toBe(900)
    expect(box(BB, cy.state).right).toBeLessThanOrEqual(900)
    cy.setContainer(1400, 900)   // closing the panel again is detected too
    fit.resized()
    expect(box(BB, cy.state).right).toBeGreaterThan(900)
  })

  it('cy.resize() re-entering the handler cannot recurse', () => {
    const cy = fakeCy(1400, 900)
    const fit = createAutoFit(cy, () => null, { animate: false })
    fit.fit()
    let reentries = 0
    cy.state.onResize = () => { reentries += 1; if (reentries < 50) fit.resized() }
    cy.setContainer(900, 900)
    fit.resized()
    expect(cy.state.resizes).toBe(1) // the nested call was ignored
    expect(box(BB, cy.state).right).toBeLessThanOrEqual(900)
  })

  it('programmatic viewport changes never count as a manual adjustment; each user gesture does', () => {
    const cy = fakeCy(1200, 800)
    const fit = createAutoFit(cy, () => null, { animate: true })
    fit.fit(200)
    cy.flush()
    cy.pan({ x: 5, y: 5 })
    cy.zoom(0.5)
    expect(fit.following).toBe(true)
    cy.setContainer(800, 800)
    fit.resized(200)
    cy.flush()
    expect(box(BB, cy.state).right).toBeLessThanOrEqual(800)
    for (const gesture of USER_VIEWPORT_EVENTS.split(' ')) {
      const gcy = fakeCy(1000, 800)
      const controller = createAutoFit(gcy, () => null)
      expect(controller.following).toBe(true)
      gcy.emit(gesture)
      expect(controller.following).toBe(false)
      controller.dispose()
    }
  })

  it('rapid successive resizes leave only the latest animation, fitted to the final size', () => {
    const cy = fakeCy(1400, 900)
    const fit = createAutoFit(cy, () => null, { animate: true })
    fit.fit(200)
    for (const w of [1300, 1100, 950, 900]) {
      cy.setContainer(w, 900)
      fit.resized(200)
    }
    expect(cy.state.queue).toHaveLength(1) // earlier animations were stopped, not left running
    expect(cy.state.stops).toBeGreaterThanOrEqual(4)
    cy.flush()
    expect(box(BB, cy.state).right).toBeLessThanOrEqual(900)
    expect(fit.following).toBe(true)
  })

  it('refits beside a tall legend and above a wide one, only while following', () => {
    const cy = fakeCy(1200, 800)
    let legend = null
    const fit = createAutoFit(cy, () => legend, { animate: false })
    fit.fit()
    legend = TALL_LEGEND
    fit.legendChanged()
    expect(box(BB, cy.state).left).toBeGreaterThanOrEqual(468) // fitted beside the legend
    legend = WIDE_LEGEND
    fit.legendChanged()
    expect(box(BB, cy.state).bottom).toBeLessThanOrEqual(800 - 112) // fitted above the legend
    const z = cy.state.zoom
    fit.resized() // same size: nothing happens
    expect(cy.state.zoom).toBe(z)
    fit.userAdjusted()
    const before = { ...cy.state.pan }
    legend = null
    fit.legendChanged()
    expect(cy.state.pan).toEqual(before)
  })
})