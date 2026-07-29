import { http, HttpResponse } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Result from './Result.jsx'
import { setSession } from '../auth/session.js'
import { server } from '../test/server.js'
import { TEST_TOKEN } from '../test/token.js'

const completedGeneration = {
  id: 'generation-1',
  product_id: 'product-1',
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
  copy: {
    language: 'fr',
    instagram: {
      text: 'Une hydratation au geste précis.\nDécouvrir.',
      hashtags: ['#SoinVisage'],
      claims: [
        {
          evidence_id: 'benefit-1',
          rendered_text: 'Une hydratation au geste précis.',
        },
      ],
    },
    facebook: {
      text: 'Hydrate la peau avec une formule à l’aloe vera.',
      hashtags: ['#MaisonLune'],
      claims: [
        {
          evidence_id: 'ingredient-1',
          rendered_text: 'Une formule à l’aloe vera.',
        },
      ],
    },
    linkedin: {
      text: 'Maison Lune présente son soin visage.',
      hashtags: [],
      claims: [
        {
          evidence_id: 'brand-1',
          rendered_text: 'Maison Lune',
        },
      ],
    },
  },
  artifacts: {
    'instagram.jpg': '/api/v1/generations/generation-1/artifacts/instagram.jpg',
    'facebook.jpg': '/api/v1/generations/generation-1/artifacts/facebook.jpg',
    'linkedin.jpg': '/api/v1/generations/generation-1/artifacts/linkedin.jpg',
  },
}

describe('completed campaign', () => {
  it('shows uncropped platform tabs, copy actions, evidence, and the bundle action', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json(completedGeneration)
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

    const { container } = render(
      <MemoryRouter initialEntries={['/result/generation-1']}>
        <Routes>
          <Route path="/result/:id" element={<Result />} />
        </Routes>
      </MemoryRouter>
    )

    expect(
      await screen.findByRole('tab', { name: 'Instagram' })
    ).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('tab', { name: 'Facebook' }))
    expect(screen.getByRole('tab', { name: 'Facebook' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(
      screen.getByRole('img', { name: /visuel facebook/i })
    ).toHaveClass('platform-viewer__image')
    expect(
      screen.getByText('Hydrate la peau avec une formule à l’aloe vera.')
    ).toBeInTheDocument()
    expect(screen.getByText('ingredient-1')).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: /copier le texte facebook/i })
    )
    expect(
      screen.getByRole('button', { name: /copier le texte facebook/i })
    ).toHaveTextContent(/^Copié$/)
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining('Hydrate la peau avec une formule à l’aloe vera.')
    )

    const evidenceTrigger = screen.getByRole('button', {
      name: /voir les preuves/i,
    })
    await user.click(evidenceTrigger)
    const dialog = await screen.findByRole('dialog', {
      name: /preuves de composition/i,
    })
    expect(dialog).toBeInTheDocument()
    expect(container).toHaveAttribute('inert')
    const close = screen.getByRole('button', { name: /fermer les preuves/i })
    expect(close).toHaveFocus()
    await user.tab()
    expect(close).toHaveFocus()
    await user.tab({ shift: true })
    expect(close).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(dialog).not.toBeInTheDocument()
    expect(container).not.toHaveAttribute('inert')
    expect(evidenceTrigger).toHaveFocus()
    expect(
      screen.getByRole('button', { name: /télécharger le dossier zip/i })
    ).toBeInTheDocument()
  })

  it('marks English campaign prose and claims with their language', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const englishGeneration = {
      ...completedGeneration,
      language: 'en',
      copy: {
        language: 'en',
        instagram: {
          text: 'Hydrates skin.',
          hashtags: ['#Skincare'],
          claims: [
            {
              evidence_id: 'benefit-1',
              rendered_text: 'Hydrates skin.',
            },
          ],
        },
        facebook: { text: 'Hydrates skin.', hashtags: [], claims: [] },
        linkedin: { text: 'Hydrates skin.', hashtags: [], claims: [] },
      },
    }

    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json(englishGeneration)
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

    render(
      <MemoryRouter initialEntries={['/result/generation-1']}>
        <Routes>
          <Route path="/result/:id" element={<Result />} />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('Texte anglais')).toBeInTheDocument()
    const prose = screen.getAllByText('Hydrates skin.')
    expect(prose).toHaveLength(2)
    prose.forEach((element) => expect(element).toHaveAttribute('lang', 'en'))
    expect(screen.getByText('#Skincare')).toHaveAttribute('lang', 'en')
    expect(
      screen.getByRole('button', { name: /copier le texte instagram/i })
    ).toHaveTextContent(/^Copier$/)
  })

  it('fails closed on missing evidence and retries the complete evidence set', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const user = userEvent.setup()
    let failOriginal = true
    let failArtifacts = true
    let originalCalls = 0
    let artifactCalls = 0

    server.use(
      http.get('*/api/v1/generations/generation-1', () =>
        HttpResponse.json(completedGeneration)
      ),
      http.get('*/api/v1/products/product-1/image', () => {
        originalCalls += 1
        if (failOriginal) {
          return HttpResponse.json(
            {
              error: {
                code: 'INTERNAL_ERROR',
                message: 'La photo originale ne répond pas.',
              },
            },
            { status: 503 }
          )
        }
        return new HttpResponse(new Uint8Array([1]), {
          headers: { 'Content-Type': 'image/jpeg' },
        })
      }),
      http.get('*/api/v1/generations/generation-1/artifacts/*', () => {
        artifactCalls += 1
        if (failArtifacts) {
          return HttpResponse.json(
            {
              error: {
                code: 'ARTIFACT_STORAGE_FAILED',
                message: 'Le dossier de preuves ne répond pas.',
              },
            },
            { status: 503 }
          )
        }
        return new HttpResponse(new Uint8Array([2]), {
          headers: { 'Content-Type': 'image/png' },
        })
      })
    )

    render(
      <MemoryRouter initialEntries={['/result/generation-1']}>
        <Routes>
          <Route path="/result/:id" element={<Result />} />
        </Routes>
      </MemoryRouter>
    )

    expect(
      await screen.findByText('Preuves indisponibles')
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: /visuel instagram indisponible/i })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('img', { name: /visuel instagram/i })
    ).not.toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: /voir les preuves/i })
    )
    const dialog = await screen.findByRole('dialog', {
      name: /preuves de composition/i,
    })
    expect(within(dialog).getAllByText('Fichier indisponible')).toHaveLength(5)

    failOriginal = false
    failArtifacts = false
    await user.click(
      within(dialog).getByRole('button', {
        name: /réessayer les preuves/i,
      })
    )

    expect(
      await screen.findByRole('img', { name: /visuel instagram/i })
    ).toBeInTheDocument()
    expect(
      within(dialog).getByRole('img', { name: /photo originale/i })
    ).toBeInTheDocument()
    expect(originalCalls).toBe(2)
    expect(artifactCalls).toBeGreaterThanOrEqual(7)
  })
})
