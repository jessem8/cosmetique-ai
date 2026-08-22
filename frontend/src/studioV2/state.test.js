import { describe, expect, it } from 'vitest'
import {
  initialStudioState,
  isGenerationReady,
  isLockReady,
  studioReducer,
} from './state.js'

describe('Campaign Studio V2 state machine', () => {
  it('walks the six bounded workflow steps in order and never leaves the range', () => {
    let state = initialStudioState()
    expect(state.step).toBe('product')

    for (const expected of ['lock', 'direction', 'generate', 'compare', 'export']) {
      state = studioReducer(state, { type: 'ADVANCE' })
      expect(state.step).toBe(expected)
    }

    state = studioReducer(state, { type: 'ADVANCE' })
    expect(state.step).toBe('export')

    for (const expected of ['compare', 'generate', 'direction', 'lock', 'product']) {
      state = studioReducer(state, { type: 'BACK' })
      expect(state.step).toBe(expected)
    }
    expect(studioReducer(state, { type: 'BACK' }).step).toBe('product')
  })

  it('clamps normalized drawing coordinates and preserves point semantics', () => {
    let state = initialStudioState()
    state = studioReducer(state, {
      type: 'SET_LOCK_BOX',
      bbox: { x: -0.2, y: 0.4, width: 1.4, height: 0.001 },
    })
    expect(state.lock.bbox).toEqual({ x: 0, y: 0.4, width: 1, height: 0.02 })

    state = studioReducer(state, {
      type: 'ADD_POINT',
      kind: 'negative',
      point: { id: 'negative-1', x: 1.4, y: -0.2 },
    })
    expect(state.lock.points).toEqual([
      { id: 'negative-1', kind: 'negative', x: 1, y: 0 },
    ])
    expect(studioReducer(state, { type: 'UNDO_POINT' }).lock.points).toEqual([])
  })

  it('requires a validated lock, provider, and bounded direction before generation', () => {
    const state = initialStudioState({
      lock: {
        id: 'lock-1',
        status: 'needs_review',
        maskUrl: '/mask.png',
        cutoutUrl: '/cutout.png',
      },
      direction: { providerId: 'provider-1', prompt: 'A clean studio scene.' },
    })
    expect(isLockReady(state.lock)).toBe(false)
    expect(isGenerationReady(state)).toBe(false)

    const validated = studioReducer(state, { type: 'SET_LOCK_STATUS', status: 'validated' })
    expect(isLockReady(validated.lock)).toBe(true)
    expect(isGenerationReady(validated)).toBe(true)
  })

  it('requires an explicit abstention reason for non-validated locks', () => {
    let state = initialStudioState()
    state = studioReducer(state, { type: 'SET_LOCK_STATUS', status: 'rejected' })
    expect(state.lock.abstentionReason).toBe('')
    state = studioReducer(state, { type: 'SET_ABSTENTION_REASON', reason: 'occlusion' })
    expect(state.lock.abstentionReason).toBe('occlusion')
    state = studioReducer(state, { type: 'SET_LOCK_STATUS', status: 'validated' })
    expect(state.lock.abstentionReason).toBe('')
  })
})
