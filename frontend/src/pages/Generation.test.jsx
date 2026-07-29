import { http, HttpResponse } from 'msw'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Generation from './Generation.jsx'
import { setSession } from '../auth/session.js'
import { server } from '../test/server.js'
import { TEST_TOKEN } from '../test/token.js'

const ambiguousGeneration = {
  id: 'generation-1',
  product_id: 'product-1',
  status: 'error',
  stage: 'analysis',
  completed_stages: [],
  language: 'fr',
  seed: 42,
  error: {
    code: 'TARGET_AMBIGUOUS',
    message: 'Plusieurs produits possibles ont été détectés.',
  },
  ambiguity: {
    original_url: '/api/v1/products/product-1/image',
    candidates: [
      {
        id: 'candidate-1',
        score: 0.91,
        x: 0.1,
        y: 0.2,
        width: 0.3,
        height: 0.6,
      },
    ],
  },
}

describe('generation status', () => {
  it('recovers an ambiguous target by creating a new complete generation', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const user = userEvent.setup()
    let retryBody

    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json(ambiguousGeneration)
      ),
      http.get('*/api/v1/products/product-1/image', () =>
        new HttpResponse(new Uint8Array([1, 2, 3]), {
          headers: { 'Content-Type': 'image/jpeg' },
        })
      ),
      http.post(
        '*/api/v1/products/product-1/generations',
        async ({ request }) => {
          retryBody = await request.json()
          return HttpResponse.json(
            { id: 'generation-2', status: 'pending' },
            { status: 202 }
          )
        }
      )
    )

    render(
      <MemoryRouter initialEntries={['/generations/generation-1']}>
        <Routes>
          <Route path="/generations/:id" element={<Generation />} />
          <Route
            path="/generations/generation-2"
            element={<h1>Nouvelle génération</h1>}
          />
        </Routes>
      </MemoryRouter>
    )

    await user.click(
      await screen.findByRole('radio', { name: /produit possible 1/i })
    )
    await user.click(
      screen.getByRole('button', { name: /utiliser ce produit/i })
    )

    expect(
      await screen.findByRole('heading', { name: 'Nouvelle génération' })
    ).toBeInTheDocument()
    expect(retryBody).toMatchObject({
      language: 'fr',
      seed: 42,
      source_generation_id: 'generation-1',
      target_hint: {
        type: 'box',
        x: 0.1,
        y: 0.2,
        width: 0.3,
        height: 0.6,
      },
    })
  })

  it('shows only the current server stage and a reconnecting state', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    let calls = 0
    server.use(
      http.get('*/api/v1/generations/generation-1', () => {
        calls += 1
        if (calls === 1) {
          return HttpResponse.json({
            id: 'generation-1',
            product_id: 'product-1',
            status: 'processing',
            stage: 'composition',
            completed_stages: [
              'analysis',
              'extraction',
              'art_direction',
              'background',
            ],
          })
        }
        return HttpResponse.error()
      })
    )

    render(
      <MemoryRouter initialEntries={['/generations/generation-1']}>
        <Routes>
          <Route path="/generations/:id" element={<Generation />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Composition des formats')).toHaveAttribute(
      'aria-current',
      'step'
    )
    expect(screen.queryByText(/%|seconde|minute/i)).not.toBeInTheDocument()
  })

  it('reuses the candidate idempotency key when a successful response is lost', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const user = userEvent.setup()
    const keys = []

    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json(ambiguousGeneration)
      ),
      http.get('*/api/v1/products/product-1/image', () =>
        new HttpResponse(new Uint8Array([1, 2, 3]), {
          headers: { 'Content-Type': 'image/jpeg' },
        })
      ),
      http.post(
        '*/api/v1/products/product-1/generations',
        ({ request }) => {
          keys.push(request.headers.get('idempotency-key'))
          if (keys.length === 1) return HttpResponse.error()
          return HttpResponse.json(
            { id: 'generation-2', status: 'pending' },
            { status: 202 }
          )
        }
      )
    )

    render(
      <MemoryRouter initialEntries={['/generations/generation-1']}>
        <Routes>
          <Route path="/generations/:id" element={<Generation />} />
          <Route
            path="/generations/generation-2"
            element={<h1>Reprise confirmée</h1>}
          />
        </Routes>
      </MemoryRouter>
    )

    await user.click(
      await screen.findByRole('radio', { name: /produit possible 1/i })
    )
    const confirm = screen.getByRole('button', {
      name: /utiliser ce produit/i,
    })
    await user.click(confirm)
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await user.click(confirm)

    expect(
      await screen.findByRole('heading', { name: 'Reprise confirmée' })
    ).toBeInTheDocument()
    expect(keys).toHaveLength(2)
    expect(keys[1]).toBe(keys[0])
  })

  it('preserves ambiguous candidates and retries a failed original image', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const user = userEvent.setup()
    let imageCalls = 0

    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json(ambiguousGeneration)
      ),
      http.get('*/api/v1/products/product-1/image', () => {
        imageCalls += 1
        if (imageCalls === 1) {
          return HttpResponse.json(
            {
              error: {
                code: 'INTERNAL_ERROR',
                message: 'La photo ne répond pas.',
              },
            },
            { status: 503 }
          )
        }
        return new HttpResponse(new Uint8Array([1, 2, 3]), {
          headers: { 'Content-Type': 'image/jpeg' },
        })
      })
    )

    render(
      <MemoryRouter initialEntries={['/generations/generation-1']}>
        <Routes>
          <Route path="/generations/:id" element={<Generation />} />
        </Routes>
      </MemoryRouter>
    )

    expect(
      await screen.findByRole('heading', {
        name: /photo originale indisponible/i,
      })
    ).toBeInTheDocument()
    expect(
      screen.getByText(/le cadre détecté reste disponible/i)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/le service ne répond pas/i)
    ).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: /réessayer la photo/i })
    )

    expect(
      await screen.findByRole('radiogroup', { name: /produits détectés/i })
    ).toBeInTheDocument()
    expect(imageCalls).toBe(2)
  })
})
