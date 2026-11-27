/**
 * L2 UI round 4 (issue #91, U7): the Graph view's minimum scale is a named
 * constant, GRAPH_MIN_SCALE = 0.75 (_orchestration/L2-UI.md, amendment). It
 * lives in model.ts with the zoom limits (react-refresh allows only
 * components in GraphView.tsx).
 */
import { describe, expect, it } from 'vitest'
import { GRAPH_MIN_SCALE, GRAPH_ZOOM_MAX, GRAPH_ZOOM_MIN } from '../../components/messages/model'

describe('Graph scale constants (U7)', () => {
  it('GRAPH_MIN_SCALE is 0.75', () => {
    expect(GRAPH_MIN_SCALE).toBe(0.75)
  })

  it('zoom stops at 0.25 and 2', () => {
    expect([GRAPH_ZOOM_MIN, GRAPH_ZOOM_MAX]).toEqual([0.25, 2])
  })
})
