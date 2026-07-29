import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import {
  API_ROOT,
  ApiError,
  generations,
  normalizeApiError,
  products,
} from './client.js'
import client from './client.js'
import { setSession } from '../auth/session.js'
import { server } from '../test/server.js'
import { TEST_TOKEN } from '../test/token.js'

describe('API v1 client contract', () => {
  it('fails closed to the exact same-origin API root', () => {
    expect(API_ROOT).toBe('/api/v1')
    expect(client.defaults.baseURL).toBe('/api/v1')
    expect(client.defaults.baseURL).not.toMatch(/^https?:\/\//)
  })

  it('creates a generation with bearer auth and an idempotency key', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })

    server.use(
      http.post(
        '*/api/v1/products/product-1/generations',
        async ({ request }) => {
          expect(request.headers.get('authorization')).toBe(
            `Bearer ${TEST_TOKEN}`
          )
          expect(request.headers.get('idempotency-key')).toBe(
            'campaign-key-0001'
          )
          expect(await request.json()).toEqual({
            language: 'fr',
            seed: 42,
          })
          return HttpResponse.json(
            { id: 'generation-1', status: 'pending' },
            { status: 202 }
          )
        }
      )
    )

    const response = await generations.create(
      'product-1',
      { language: 'fr', seed: 42 },
      { idempotencyKey: 'campaign-key-0001' }
    )

    expect(response.data.id).toBe('generation-1')
  })

  it('uses protected blob endpoints for artifacts and the validated bundle', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })

    server.use(
      http.get(
        '*/api/v1/generations/generation-1/artifacts/instagram.jpg',
        ({ request }) => {
          expect(request.headers.get('authorization')).toBe(
            `Bearer ${TEST_TOKEN}`
          )
          return new HttpResponse(new Uint8Array([1, 2, 3]), {
            headers: { 'Content-Type': 'image/jpeg' },
          })
        }
      ),
      http.get(
        '*/api/v1/generations/generation-1/bundle',
        ({ request }) => {
          expect(request.headers.get('authorization')).toBe(
            `Bearer ${TEST_TOKEN}`
          )
          return new HttpResponse(new Uint8Array([4, 5, 6]), {
            headers: { 'Content-Type': 'application/zip' },
          })
        }
      )
    )

    const [artifact, bundle] = await Promise.all([
      generations.getArtifact('generation-1', 'instagram.jpg'),
      generations.getBundle('generation-1'),
    ])

    expect(artifact.data).toBeInstanceOf(Blob)
    expect(bundle.data).toBeInstanceOf(Blob)
  })

  it('uploads a product through the same-origin versioned API', async () => {
    let configuredContentType
    const interceptor = client.interceptors.request.use((config) => {
      configuredContentType = config.headers.get('Content-Type')
      return config
    })
    server.use(
      http.post('*/api/v1/products', () => {
        return HttpResponse.json({ id: 'product-1' }, { status: 201 })
      })
    )

    const body = new FormData()
    body.set('name', 'Sérum perle')
    body.set('category', 'soin_visage')

    let response
    try {
      response = await products.create(body)
    } finally {
      client.interceptors.request.eject(interceptor)
    }

    expect(response.data.id).toBe('product-1')
    expect(configuredContentType).toBeUndefined()
  })

  it('normalizes stable server errors without leaking raw response details', () => {
    const error = normalizeApiError({
      response: {
        status: 503,
        data: {
          error: {
            code: 'AI_SERVICE_UNAVAILABLE',
            message: 'Le studio IA est indisponible.',
            retryable: true,
          },
          traceback: 'must not escape',
        },
      },
      config: { method: 'post', url: '/products/1/generations' },
    })

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 503,
      code: 'AI_SERVICE_UNAVAILABLE',
      message: 'Le studio IA est indisponible.',
      retryable: true,
    })
    expect(JSON.stringify(error)).not.toContain('traceback')
  })
})
