import { http, HttpResponse } from 'msw'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'vitest-axe'
import { describe, expect, it } from 'vitest'
import App from './App.jsx'
import { setSession } from './auth/session.js'
import { server } from './test/server.js'
import { TEST_TOKEN } from './test/token.js'

const authenticate = () =>
  setSession({ token: TEST_TOKEN, email: 'studio@example.com' })

const renderApp = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  )

const expectNoAxeViolations = async (container) => {
  const results = await axe(container, {
    // jsdom has no layout canvas. Playwright runs real color-contrast checks
    // across every configured desktop, tablet, and mobile project.
    rules: { 'color-contrast': { enabled: false } },
  })
  expect(results.violations).toEqual([])
}

describe('route accessibility', () => {
  it('has no detectable axe violations on authentication', async () => {
    const { container } = renderApp('/login')

    await screen.findByRole('heading', { name: /accéder au studio/i })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations in the empty campaign library', async () => {
    authenticate()
    server.use(
      http.get('*/api/v1/generations', () =>
        HttpResponse.json({ items: [], next_cursor: null })
      )
    )
    const { container } = renderApp('/dashboard')

    await screen.findByRole('heading', { name: /votre première campagne/i })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations in the campaign brief', async () => {
    authenticate()
    const { container } = renderApp('/new')

    await screen.findByRole('heading', {
      name: /donnez au produit toute la scène/i,
    })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations in candidate selection', async () => {
    authenticate()
    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json({
          id: 'generation-1',
          product_id: 'product-1',
          product: {
            name: 'Sérum perle',
            brand: 'Maison Lune',
            category: 'soin_visage',
          },
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
                type: 'box',
                x: 0.08,
                y: 0.12,
                width: 0.35,
                height: 0.72,
              },
              {
                id: 'candidate-2',
                score: 0.86,
                type: 'box',
                x: 0.55,
                y: 0.18,
                width: 0.32,
                height: 0.65,
              },
            ],
          },
        })
      ),
      http.get('*/api/v1/products/product-1/image', () =>
        new HttpResponse(new Uint8Array([1]), {
          headers: { 'Content-Type': 'image/jpeg' },
        })
      )
    )
    const { container } = renderApp('/generations/generation-1')

    await screen.findByRole('radiogroup', { name: /produits détectés/i })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations in a completed result', async () => {
    authenticate()
    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json({
          id: 'generation-1',
          product_id: 'product-1',
          product: {
            name: 'Sérum perle',
            brand: 'Maison Lune',
            category: 'soin_visage',
          },
          status: 'done',
          stage: 'packaging',
          completed_stages: [
            'analysis',
            'extraction',
            'art_direction',
            'background',
            'composition',
            'copy',
            'export',
            'packaging',
          ],
          language: 'fr',
          seed: 42,
          copy: {
            language: 'fr',
            instagram: {
              text: 'Hydrate la peau.',
              hashtags: ['#SoinVisage'],
              claims: [
                {
                  evidence_id: 'benefit-1',
                  rendered_text: 'Hydrate la peau.',
                },
              ],
            },
            facebook: {
              text: 'Hydrate la peau.',
              hashtags: [],
              claims: [],
            },
            linkedin: {
              text: 'Hydrate la peau.',
              hashtags: [],
              claims: [],
            },
          },
          artifacts: [],
        })
      ),
      http.get('*/api/v1/products/product-1/image', () =>
        new HttpResponse(new Uint8Array([1]), {
          headers: { 'Content-Type': 'image/jpeg' },
        })
      ),
      http.get('*/api/v1/generations/generation-1/artifacts/*', () =>
        new HttpResponse(new Uint8Array([2]), {
          headers: { 'Content-Type': 'image/png' },
        })
      )
    )
    const { container } = renderApp('/result/generation-1')

    await screen.findByRole('tab', { name: 'Instagram' })
    await screen.findByRole('img', { name: /visuel instagram/i })
    await expectNoAxeViolations(container)
  })
})
