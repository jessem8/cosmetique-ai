import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server.js'
import { setSession } from '../auth/session.js'
import { TEST_TOKEN } from '../test/token.js'
import { studioV2, STUDIO_V2_ENDPOINTS } from './client.js'

describe('Campaign Studio V2 API boundary', () => {
  it('keeps product lock lifecycle endpoints isolated behind the client map', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const calls = []
    server.use(
      http.post('*/api/v1/studio/v2/product-locks', async ({ request }) => {
        calls.push({ method: request.method, path: new URL(request.url).pathname, body: await request.json() })
        return HttpResponse.json({ id: 'lock-1', status: 'needs_review', revision: 1 })
      }),
      http.post('*/api/v1/studio/v2/product-locks/lock-1/refine', async ({ request }) => {
        calls.push({ method: request.method, path: new URL(request.url).pathname, body: await request.json() })
        return HttpResponse.json({ id: 'lock-1', status: 'needs_review', revision: 2 })
      }),
      http.post('*/api/v1/studio/v2/product-locks/lock-1/validate', () => HttpResponse.json({ id: 'lock-1', status: 'validated', revision: 2 })),
    )

    await studioV2.productLocks.create({ source_asset_id: 'fixture-product' })
    await studioV2.productLocks.refine('lock-1', { revision: 1 })
    await studioV2.productLocks.validate('lock-1')

    expect(STUDIO_V2_ENDPOINTS.productLocks).toBe('/studio/v2/product-locks')
    expect(calls).toEqual([
      { method: 'POST', path: '/api/v1/studio/v2/product-locks', body: { source_asset_id: 'fixture-product' } },
      { method: 'POST', path: '/api/v1/studio/v2/product-locks/lock-1/refine', body: { revision: 1 } },
    ])
  })

  it('supports provider profiles, generation lifecycle, variants, and export', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const calls = []
    server.use(
      http.get('*/api/v1/studio/v2/provider-profiles', () => HttpResponse.json({ providers: [{ id: 'safe-v1', capabilities: ['mask-preservation'] }] })),
      http.post('*/api/v1/studio/v2/generations', async ({ request }) => {
        calls.push(await request.json())
        return HttpResponse.json({ id: 'generation-1', status: 'queued', stage: 'preparing' })
      }),
      http.get('*/api/v1/studio/v2/generations/generation-1', () => HttpResponse.json({ id: 'generation-1', status: 'done', stage: 'qa' })),
      http.post('*/api/v1/studio/v2/generations/generation-1/cancel', () => HttpResponse.json({ id: 'generation-1', status: 'canceled' })),
      http.get('*/api/v1/studio/v2/generations/generation-1/variants', () => HttpResponse.json({ variants: [{ id: 'variant-1', image_url: '/fixture.svg' }] })),
      http.get('*/api/v1/studio/v2/generations/generation-1/export', () => new HttpResponse(new Blob(['zip-fixture'], { type: 'application/zip' }), { status: 200 })),
    )

    const profiles = await studioV2.providerProfiles.list()
    const generation = await studioV2.generations.create({ product_lock_id: 'lock-1', variant_count: 2 })
    const status = await studioV2.generations.get(generation.data.id)
    const variants = await studioV2.generations.variants(generation.data.id)
    await studioV2.generations.cancel(generation.data.id)
    const bundle = await studioV2.generations.exportBundle(generation.data.id)

    expect(profiles.data.providers[0].id).toBe('safe-v1')
    expect(status.data.status).toBe('done')
    expect(variants.data.variants[0].id).toBe('variant-1')
    expect(bundle.data).toBeInstanceOf(Blob)
    expect(calls).toEqual([{ product_lock_id: 'lock-1', variant_count: 2 }])
  })
})
