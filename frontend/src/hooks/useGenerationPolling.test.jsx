import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useGenerationPolling } from './useGenerationPolling.js'

const setVisibility = (value) => {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value,
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('useGenerationPolling', () => {
  afterEach(() => setVisibility('visible'))

  it('polls immediately, every 2.5s while visible, and every 10s while hidden', async () => {
    vi.useFakeTimers()
    const fetchGeneration = vi.fn().mockResolvedValue({
      id: 'generation-1',
      status: 'processing',
      stage: 'background',
    })

    renderHook(() =>
      useGenerationPolling('generation-1', {
        fetchGeneration,
      })
    )

    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500)
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(2)

    setVisibility('hidden')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9999)
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(3)
  })

  it('stops after a terminal response and aborts the active request on cleanup', async () => {
    vi.useFakeTimers()
    let capturedSignal
    const fetchGeneration = vi.fn((_id, { signal }) => {
      capturedSignal = signal
      return Promise.resolve({
        id: 'generation-1',
        status: 'done',
        stage: 'packaging',
      })
    })

    const { unmount } = renderHook(() =>
      useGenerationPolling('generation-1', { fetchGeneration })
    )

    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000)
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(1)

    unmount()
    expect(capturedSignal.aborted).toBe(true)
  })

  it('keeps one request in flight across manual refresh and visibility changes', async () => {
    vi.useFakeTimers()
    let resolveSlowRequest
    const fetchGeneration = vi
      .fn()
      .mockResolvedValueOnce({
        id: 'generation-1',
        status: 'processing',
        stage: 'analysis',
      })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSlowRequest = resolve
          })
      )
      .mockResolvedValue({
        id: 'generation-1',
        status: 'processing',
        stage: 'background',
      })

    const { result } = renderHook(() =>
      useGenerationPolling('generation-1', { fetchGeneration })
    )

    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
      result.current.refresh()
      await Promise.resolve()
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(2)

    setVisibility('hidden')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(2)

    await act(async () => {
      resolveSlowRequest({
        id: 'generation-1',
        status: 'processing',
        stage: 'composition',
      })
      await Promise.resolve()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9999)
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(fetchGeneration).toHaveBeenCalledTimes(3)
  })
})
