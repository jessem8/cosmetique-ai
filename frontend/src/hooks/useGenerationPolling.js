import { useCallback, useEffect, useRef, useState } from 'react'
import { generations } from '../api/client.js'

const TERMINAL_STATUSES = new Set(['done', 'error'])

const defaultFetchGeneration = async (id, { signal }) => {
  const response = await generations.get(id, { signal })
  return response.data
}

export const useGenerationPolling = (
  generationId,
  {
    fetchGeneration = defaultFetchGeneration,
    visibleInterval = 2500,
    hiddenInterval = 10_000,
  } = {}
) => {
  const [generation, setGeneration] = useState(null)
  const [error, setError] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isReconnecting, setIsReconnecting] = useState(false)
  const refreshRef = useRef(null)

  useEffect(() => {
    if (!generationId) {
      setGeneration(null)
      setIsLoading(false)
      return undefined
    }

    let active = true
    let timer = null
    let controller = null
    let terminal = false
    let inFlight = false
    let requestVersion = 0

    const clearTimer = () => {
      if (timer !== null) window.clearTimeout(timer)
      timer = null
    }

    const schedule = () => {
      clearTimer()
      if (!active || terminal) return
      const delay =
        document.visibilityState === 'hidden'
          ? hiddenInterval
          : visibleInterval
      timer = window.setTimeout(run, delay)
    }

    const run = async () => {
      clearTimer()
      if (!active || terminal) return
      controller?.abort()
      const requestController = new AbortController()
      const version = ++requestVersion
      controller = requestController
      inFlight = true

      try {
        const next = await fetchGeneration(generationId, {
          signal: requestController.signal,
        })
        if (
          !active ||
          requestController.signal.aborted ||
          version !== requestVersion
        ) {
          return
        }
        setGeneration(next)
        setError(null)
        setIsReconnecting(false)
        terminal = TERMINAL_STATUSES.has(next?.status)
      } catch (requestError) {
        if (
          !active ||
          requestController.signal.aborted ||
          version !== requestVersion
        ) {
          return
        }
        setError(requestError)
        setIsReconnecting(true)
      } finally {
        if (version === requestVersion) {
          inFlight = false
        }
        if (active && version === requestVersion) {
          setIsLoading(false)
          schedule()
        }
      }
    }

    const handleVisibilityChange = () => {
      if (!active || terminal) return
      clearTimer()
      if (!inFlight) schedule()
    }

    refreshRef.current = run
    document.addEventListener('visibilitychange', handleVisibilityChange)
    run()

    return () => {
      active = false
      clearTimer()
      controller?.abort()
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      refreshRef.current = null
    }
  }, [fetchGeneration, generationId, hiddenInterval, visibleInterval])

  const refresh = useCallback(() => refreshRef.current?.(), [])

  return {
    generation,
    error,
    isLoading,
    isReconnecting,
    refresh,
  }
}
