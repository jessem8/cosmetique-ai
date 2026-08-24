import { http, HttpResponse } from 'msw'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import StudioWorkspace from './StudioWorkspace.jsx'
import { server } from '../test/server.js'

const renderWorkspace = () => render(<MemoryRouter initialEntries={['/new']}><StudioWorkspace /></MemoryRouter>)

const lockResponse = {
  id: 'lock-1',
  status: 'needs_review',
  revision: 1,
  source_image_url: '/api/v1/products/product-1/image',
  mask_url: '/api/v1/studio/v2/product-locks/lock-1/artifacts/mask.png',
  cutout_url: '/api/v1/studio/v2/product-locks/lock-1/artifacts/cutout.png',
  target_box: { type: 'box', x: 0.25, y: 0.15, width: 0.4, height: 0.6 },
  metrics: { accepted: 1, score: 0.92, coverage_fraction: 0.08, connected_components: 1 },
  model_provenance: { pipeline: 'ai_runtime', algorithm: 'canonical-binary-mask-v1', segmenter_model: 'u2net-onnx', provider: 'onnxruntime' },
}

const registerExtractionHandlers = () => {
  server.use(
    http.get('*/api/v1/studio/v2/engine/status', () => HttpResponse.json({ status: 'ready', ready: true, gpu: 'CPU U2Net', engine_mode: 'cpu-u2net', models: ['U2Net ONNX (CPU)'] })),
    http.post('*/api/v1/products', ({ request }) => {
      expect(request.headers.get('content-type')).toMatch(/multipart\/form-data/i)
      return HttpResponse.json({ id: 'product-1', name: 'Test gloss', category: 'makeup', image_url: '/api/v1/products/product-1/image', source_sha256: 'a'.repeat(64) }, { status: 201 })
    }),
    http.post('*/api/v1/studio/v2/product-locks', () => HttpResponse.json(lockResponse, { status: 201 })),
    http.get('*/api/v1/studio/v2/product-locks/lock-1/artifacts/:artifact', () => new HttpResponse(new Blob(['png'], { type: 'image/png' }))),
  )
}

const createExtraction = async (user) => {
  const file = new File(['source'], 'pink-gloss.png', { type: 'image/png' })
  await user.upload(screen.getByLabelText(/choisir une photo/i), file)
  await user.type(screen.getByLabelText(/nom du produit/i), 'Test gloss')
  await user.click(screen.getByRole('button', { name: /extraire le produit/i }))
  expect(await screen.findByRole('heading', { name: /vérifiez le produit extrait/i })).toBeInTheDocument()
}

describe('Product extraction workspace', () => {
  it('opens the real source stage with truthful engine state and gated extraction', async () => {
    server.use(
      http.get('*/api/v1/studio/v2/engine/status', () => HttpResponse.json({
        status: 'ready',
        ready: true,
        gpu: 'CUDA disponible',
        runtime: 'private-ai-runtime',
        engine_mode: 'native-sam2',
        models: ['Grounding DINO', 'SAM2.1'],
      })),
    )

    renderWorkspace()

    expect(await screen.findByRole('heading', { name: /produit, isolé proprement/i })).toBeInTheDocument()
    expect(screen.getByText('Runtime prêt')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /extraire le produit/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /produit extrait/i })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /continuer vers le décor/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /générer le décor/i })).not.toBeInTheDocument()
  })

  it('uploads a real source, exposes the job status, and stops at extraction evidence', async () => {
    const user = userEvent.setup()
    registerExtractionHandlers()
    renderWorkspace()

    await createExtraction(user)

    expect(screen.getByRole('heading', { name: 'Extraction terminée' })).toBeInTheDocument()
    expect(screen.getByText('Extraction terminée. Vérifiez le masque et le détourage transparent.')).toBeInTheDocument()
    expect(screen.getByAltText('Produit extrait sur fond transparent')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /générer|décor|closerouter/i })).not.toBeInTheDocument()
  })

  it('cancels a brush draft and saves a bounded correction as a new revision', async () => {
    const user = userEvent.setup()
    registerExtractionHandlers()
    server.use(
      http.post('*/api/v1/studio/v2/product-locks/lock-1/refine', async ({ request }) => {
        const body = await request.json()
        expect(body.negative_points).toHaveLength(1)
        expect(body.negative_points[0]).toEqual({ x: 0.3, y: 0.4 })
        return HttpResponse.json({ ...lockResponse, revision: 2 }, { status: 201 })
      }),
    )
    renderWorkspace()
    await createExtraction(user)

    await user.click(screen.getByRole('button', { name: /corriger le masque/i }))
    expect(screen.getByRole('button', { name: 'Annuler' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /enregistrer et recalculer/i })).toBeDisabled()

    const canvas = screen.getByRole('img', { name: /passez le pinceau/i })
    Object.defineProperty(canvas, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, top: 0, width: 100, height: 100 }),
    })
    await user.pointer([{ target: canvas }, { coords: { clientX: 30, clientY: 40 } }, '[MouseLeft]'])
    expect(screen.getByRole('button', { name: /enregistrer et recalculer/i })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Annuler' }))
    expect(screen.getByText('Correction annulée. Le masque confirmé n’a pas été modifié.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /corriger le masque/i })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /corriger le masque/i }))
    const reopenedCanvas = screen.getByRole('img', { name: /passez le pinceau/i })
    Object.defineProperty(reopenedCanvas, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, top: 0, width: 100, height: 100 }),
    })
    await user.pointer([{ target: reopenedCanvas }, { coords: { clientX: 30, clientY: 40 } }, '[MouseLeft]'])
    await user.click(screen.getByRole('button', { name: /enregistrer et recalculer/i }))

    expect(await screen.findByText('Correction enregistrée. Vérifiez la nouvelle révision avant de la valider.')).toBeInTheDocument()
    expect(screen.getByText('R2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /corriger le masque/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /enregistrer le produit extrait/i })).toBeEnabled()
  })
})
