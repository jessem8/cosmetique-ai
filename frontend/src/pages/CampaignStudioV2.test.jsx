import { http, HttpResponse } from 'msw'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import CampaignStudioV2 from './CampaignStudioV2.jsx'
import { server } from '../test/server.js'

const providerHandler = http.get('*/api/v1/studio/v2/provider-profiles', () =>
  HttpResponse.json([
    {
      id: 'closerouter:openai:openai/gpt-image-2',
      name: 'OpenAI image edit',
      model: 'openai/gpt-image-2',
      selection: { provider: 'closerouter', profile: 'openai', model: 'openai/gpt-image-2' },
      capabilities: {
        estimated_cost_per_variant_micros: 1000000,
        max_budget_micros: 2000000,
        max_variants: 1,
        supports_mask_preservation: true,
        supports_scene_generation: true,
        supports_custom_direction: true,
      },
      capability_flags: ['mask-preservation', 'scene-generation', 'qa-provenance'],
    },
  ])
)

const privateArtifactHandlers = [
  http.get('*/api/v1/products/product-live-1/image', () => new HttpResponse(new Uint8Array([1]), { headers: { 'Content-Type': 'image/jpeg' } })),
  http.get('*/api/v1/studio/v2/product-locks/lock-live-1/artifacts/mask.png', () => new HttpResponse(new Uint8Array([2]), { headers: { 'Content-Type': 'image/png' } })),
  http.get('*/api/v1/studio/v2/product-locks/lock-live-1/artifacts/cutout.png', () => new HttpResponse(new Uint8Array([3]), { headers: { 'Content-Type': 'image/png' } })),
]

const lockResponse = (status = 'needs_review') => ({
  id: 'lock-live-1',
  product_id: 'product-live-1',
  revision: 1,
  status,
  source: {
    sha256: 'a'.repeat(64),
    mime: 'image/jpeg',
    width: 640,
    height: 1136,
    exif_orientation: 1,
  },
  target_box: { type: 'box', x: 0.2, y: 0.2, width: 0.6, height: 0.6 },
  source_image_url: '/api/v1/products/product-live-1/image',
  mask_url: '/api/v1/studio/v2/product-locks/lock-live-1/artifacts/mask.png',
  cutout_url: '/api/v1/studio/v2/product-locks/lock-live-1/artifacts/cutout.png',
  candidates: [{ id: 'lock-live-1', label: 'Segmentation candidate', confidence: 0.81, box: { type: 'box', x: 0.2, y: 0.2, width: 0.6, height: 0.6 } }],
  metrics: { score: 0.81, accepted: status === 'validated' ? 1 : 0 },
  model_provenance: { pipeline: 'ai_service', segmenter_provider: 'rembg', segmenter_model: 'isnet-general-use' },
})

const renderStudio = () =>
  render(
    <MemoryRouter initialEntries={['/new']}>
      <CampaignStudioV2 />
    </MemoryRouter>
  )

const uploadRealSource = async (user) => {
  const file = new File(['real-image-bytes'], 'p218.jpg', { type: 'image/jpeg' })
  await user.upload(screen.getByLabelText(/choose source/i), file)
  await user.type(screen.getByLabelText(/product name/i), 'L’Oréal True Match Blush')
}

describe('Campaign Studio integrated workspace', () => {
  it('stores a real upload before creating and refining a product lock', async () => {
    const user = userEvent.setup()
    let refinePayload
    server.use(
      providerHandler,
      ...privateArtifactHandlers,
      http.post('*/api/v1/products', () => HttpResponse.json({
          id: 'product-live-1',
          name: 'L’Oréal True Match Blush',
          brand: 'L’Oréal',
          category: 'makeup',
          image_url: '/api/v1/products/product-live-1/image',
          source_sha256: 'a'.repeat(64),
          source_width: 640,
          source_height: 1136,
          source_exif_orientation: 1,
        }, { status: 201 })),
      http.post('*/api/v1/studio/v2/product-locks', () => HttpResponse.json(lockResponse())),
      http.post('*/api/v1/studio/v2/product-locks/lock-live-1/refine', async ({ request }) => {
        refinePayload = await request.json()
        return HttpResponse.json({ ...lockResponse(), revision: 2 })
      }),
    )

    renderStudio()
    await uploadRealSource(user)
    await user.click(screen.getByRole('button', { name: /create product lock/i }))

    expect(await screen.findByRole('heading', { name: 'Review what stays in the image' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /save corrections/i }))
    await waitFor(() => expect(refinePayload).toMatchObject({
      target_box: { x: 0.2, y: 0.2, width: 0.6, height: 0.6 },
      positive_points: [],
      negative_points: [],
    }))
    expect(screen.getByText(/new Product Lock revision is ready for review/i)).toBeInTheDocument()
  })

  it('does not validate without real mask artifacts and exposes the measured provider profile', async () => {
    const user = userEvent.setup()
    server.use(
      providerHandler,
      ...privateArtifactHandlers,
      http.post('*/api/v1/products', () => HttpResponse.json({
        id: 'product-live-1',
        name: 'Blush',
        category: 'makeup',
        image_url: '/api/v1/products/product-live-1/image',
      }, { status: 201 })),
      http.post('*/api/v1/studio/v2/product-locks', () => HttpResponse.json({ id: 'lock-no-artifacts', status: 'needs_review', revision: 1 })),
    )
    renderStudio()
    await uploadRealSource(user)
    await user.click(screen.getByRole('button', { name: /create product lock/i }))
    await screen.findByRole('heading', { name: 'Review what stays in the image' })
    await user.click(screen.getByRole('button', { name: /validate product lock/i }))
    expect(screen.getByRole('status')).toHaveTextContent(/real mask and cutout are required/i)
    expect(await screen.findByText(/openai\/gpt-image-2/i)).toBeInTheDocument()
  })

  it('shows an explicit provider error instead of fabricating a completed variant', async () => {
    const user = userEvent.setup()
    server.use(
      providerHandler,
      ...privateArtifactHandlers,
      http.post('*/api/v1/products', () => HttpResponse.json({ id: 'product-live-1', name: 'Blush', category: 'makeup', image_url: '/api/v1/products/product-live-1/image' }, { status: 201 })),
      http.post('*/api/v1/studio/v2/product-locks', () => HttpResponse.json(lockResponse())),
      http.post('*/api/v1/studio/v2/product-locks/lock-live-1/validate', () => HttpResponse.json(lockResponse('validated'))),
      http.post('*/api/v1/studio/v2/generations', () => HttpResponse.json({ error: { code: 'RATE_LIMITED', message: 'Provider busy.' } }, { status: 429 })),
    )
    renderStudio()
    await uploadRealSource(user)
    await user.click(screen.getByRole('button', { name: /create product lock/i }))
    await screen.findByRole('heading', { name: 'Review what stays in the image' })
    await user.click(screen.getByRole('button', { name: /validate product lock/i }))
    await screen.findByRole('heading', { name: 'Describe the scene' })
    expect(screen.queryByText('Estimate')).not.toBeInTheDocument()
    expect(screen.queryByText('Ceiling')).not.toBeInTheDocument()
    expect(screen.getByText(/one provider request/i)).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText(/describe light/i), 'Soft editorial light with copy-safe space.')
    await user.click(screen.getByRole('button', { name: /review generation/i }))
    await user.click(screen.getByRole('button', { name: /generate image/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/rate limited/i)
    expect(screen.queryByText(/fixture/i)).not.toBeInTheDocument()
  })
})
