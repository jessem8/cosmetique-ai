import { http, HttpResponse } from 'msw'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Upload from './Upload.jsx'
import { setSession } from '../auth/session.js'
import { server } from '../test/server.js'
import { TEST_TOKEN } from '../test/token.js'

describe('campaign creation', () => {
  it('collects verified inputs and starts one idempotent campaign', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const user = userEvent.setup()
    let receivedGeneration

    server.use(
      http.post('*/api/v1/products', () =>
        HttpResponse.json({ id: 'product-1' }, { status: 201 })
      ),
      http.post(
        '*/api/v1/products/product-1/generations',
        async ({ request }) => {
          receivedGeneration = {
            body: await request.json(),
            key: request.headers.get('idempotency-key'),
          }
          return HttpResponse.json(
            { id: 'generation-1', status: 'pending' },
            { status: 202 }
          )
        }
      )
    )

    render(
      <MemoryRouter initialEntries={['/new']}>
        <Routes>
          <Route path="/new" element={<Upload />} />
          <Route
            path="/generations/:id"
            element={<h1>Campagne lancée</h1>}
          />
        </Routes>
      </MemoryRouter>
    )

    await user.upload(
      screen.getByLabelText(/photo du produit/i),
      new File(['image'], 'serum.jpg', { type: 'image/jpeg' })
    )
    await user.type(screen.getByLabelText(/nom du produit/i), 'Sérum perle')
    await user.selectOptions(
      screen.getByLabelText(/langue de la campagne/i),
      'en'
    )
    await user.type(
      screen.getByLabelText(/bénéfices vérifiés/i),
      'Hydrate la peau'
    )
    await user.type(
      screen.getByLabelText(/ingrédients vérifiés/i),
      'Aloe vera'
    )
    await user.type(
      screen.getByLabelText(/allégations vérifiées/i),
      'Testé sous contrôle dermatologique'
    )
    await user.click(
      screen.getByRole('button', { name: /lancer la campagne/i })
    )

    expect(
      await screen.findByRole('heading', { name: 'Campagne lancée' })
    ).toBeInTheDocument()
    expect(receivedGeneration.key).toMatch(/^campaign-/)
    expect(receivedGeneration.body).toMatchObject({
      language: 'en',
      benefits: ['Hydrate la peau'],
      ingredients: ['Aloe vera'],
      verified_claims: ['Testé sous contrôle dermatologique'],
    })
  })

  it('retries generation creation without uploading a duplicate product', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    const user = userEvent.setup()
    let uploads = 0
    let generationAttempts = 0
    const keys = []

    server.use(
      http.post('*/api/v1/products', () => {
        uploads += 1
        return HttpResponse.json({ id: 'product-1' }, { status: 201 })
      }),
      http.post('*/api/v1/products/product-1/generations', ({ request }) => {
        generationAttempts += 1
        keys.push(request.headers.get('idempotency-key'))
        if (generationAttempts === 1) {
          return HttpResponse.json(
            {
              error: {
                code: 'AI_SERVICE_UNAVAILABLE',
                message: 'Le studio IA est momentanément indisponible.',
                retryable: true,
              },
            },
            { status: 503 }
          )
        }
        return HttpResponse.json(
          { id: 'generation-2', status: 'pending' },
          { status: 202 }
        )
      })
    )

    render(
      <MemoryRouter initialEntries={['/new']}>
        <Routes>
          <Route path="/new" element={<Upload />} />
          <Route
            path="/generations/:id"
            element={<h1>Campagne relancée</h1>}
          />
        </Routes>
      </MemoryRouter>
    )

    await user.upload(
      screen.getByLabelText(/photo du produit/i),
      new File(['image'], 'serum.jpg', { type: 'image/jpeg' })
    )
    await user.type(screen.getByLabelText(/nom du produit/i), 'Sérum perle')
    await user.click(
      screen.getByRole('button', { name: /lancer la campagne/i })
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'momentanément indisponible'
    )

    await user.click(
      screen.getByRole('button', { name: /réessayer le lancement/i })
    )
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Campagne relancée' })).toBeInTheDocument()
    })
    expect(uploads).toBe(1)
    expect(generationAttempts).toBe(2)
    expect(keys[1]).toBe(keys[0])
  })

  it('rotates the generation key after a brief edit without reuploading the product', async () => {
    const user = userEvent.setup()
    let uploads = 0
    const attempts = []

    server.use(
      http.post('*/api/v1/products', () => {
        uploads += 1
        return HttpResponse.json({ id: 'product-1' }, { status: 201 })
      }),
      http.post(
        '*/api/v1/products/product-1/generations',
        async ({ request }) => {
          attempts.push({
            key: request.headers.get('idempotency-key'),
            body: await request.json(),
          })
          if (attempts.length === 1) {
            return HttpResponse.json(
              {
                error: {
                  code: 'AI_SERVICE_UNAVAILABLE',
                  message: 'Le studio IA est momentanément indisponible.',
                },
              },
              { status: 503 }
            )
          }
          return HttpResponse.json(
            { id: 'generation-2', status: 'pending' },
            { status: 202 }
          )
        }
      )
    )

    render(
      <MemoryRouter initialEntries={['/new']}>
        <Routes>
          <Route path="/new" element={<Upload />} />
          <Route
            path="/generations/:id"
            element={<h1>Brief modifié lancé</h1>}
          />
        </Routes>
      </MemoryRouter>
    )

    await user.upload(
      screen.getByLabelText(/photo du produit/i),
      new File(['image'], 'serum.jpg', { type: 'image/jpeg' })
    )
    await user.type(screen.getByLabelText(/nom du produit/i), 'Sérum perle')
    await user.click(
      screen.getByRole('button', { name: /lancer la campagne/i })
    )
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await user.type(screen.getByLabelText(/audience/i), 'Peaux sensibles')
    await user.click(
      screen.getByRole('button', { name: /réessayer le lancement/i })
    )

    expect(
      await screen.findByRole('heading', { name: 'Brief modifié lancé' })
    ).toBeInTheDocument()
    expect(uploads).toBe(1)
    expect(attempts).toHaveLength(2)
    expect(attempts[1].key).not.toBe(attempts[0].key)
    expect(attempts[1].body).toMatchObject({ audience: 'Peaux sensibles' })
  })

  it('uploads a new product after an identity edit and cannot retry the stale product', async () => {
    const user = userEvent.setup()
    let uploads = 0
    const keys = []

    server.use(
      http.post('*/api/v1/products', () => {
        uploads += 1
        return HttpResponse.json(
          { id: uploads === 1 ? 'product-1' : 'product-2' },
          { status: 201 }
        )
      }),
      http.post(
        '*/api/v1/products/product-1/generations',
        ({ request }) => {
          keys.push(request.headers.get('idempotency-key'))
          return HttpResponse.json(
            {
              error: {
                code: 'AI_SERVICE_UNAVAILABLE',
                message: 'Le studio IA est momentanément indisponible.',
              },
            },
            { status: 503 }
          )
        }
      ),
      http.post(
        '*/api/v1/products/product-2/generations',
        ({ request }) => {
          keys.push(request.headers.get('idempotency-key'))
          return HttpResponse.json(
            { id: 'generation-2', status: 'pending' },
            { status: 202 }
          )
        }
      )
    )

    render(
      <MemoryRouter initialEntries={['/new']}>
        <Routes>
          <Route path="/new" element={<Upload />} />
          <Route
            path="/generations/:id"
            element={<h1>Nouveau produit lancé</h1>}
          />
        </Routes>
      </MemoryRouter>
    )

    await user.upload(
      screen.getByLabelText(/photo du produit/i),
      new File(['image'], 'serum.jpg', { type: 'image/jpeg' })
    )
    const name = screen.getByLabelText(/nom du produit/i)
    await user.type(name, 'Sérum perle')
    await user.click(
      screen.getByRole('button', { name: /lancer la campagne/i })
    )
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await user.clear(name)
    await user.type(name, 'Crème perle')
    expect(
      screen.queryByRole('button', { name: /réessayer le lancement/i })
    ).not.toBeInTheDocument()
    await user.click(
      screen.getByRole('button', { name: /lancer la campagne/i })
    )

    expect(
      await screen.findByRole('heading', { name: 'Nouveau produit lancé' })
    ).toBeInTheDocument()
    expect(uploads).toBe(2)
    expect(keys[1]).not.toBe(keys[0])
  })

  it('rejects decimal seeds instead of silently truncating them', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>
    )

    await user.upload(
      screen.getByLabelText(/photo du produit/i),
      new File(['image'], 'serum.jpg', { type: 'image/jpeg' })
    )
    await user.type(screen.getByLabelText(/nom du produit/i), 'Sérum perle')
    const seed = screen.getByLabelText(/graine créative/i)
    await user.clear(seed)
    await user.type(seed, '1.2')
    await user.click(
      screen.getByRole('button', { name: /lancer la campagne/i })
    )

    expect(
      screen.getByText(/utilisez un nombre entier/i)
    ).toBeInTheDocument()
  })

  it('clears a previous preview when its replacement file is invalid', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>
    )

    const input = screen.getByLabelText(/photo du produit/i)
    await user.upload(
      input,
      new File(['image'], 'serum.jpg', { type: 'image/jpeg' })
    )
    expect(screen.getByAltText(/aperçu du produit/i)).toBeInTheDocument()

    fireEvent.change(input, {
      target: {
        files: [new File(['not-image'], 'notes.txt', { type: 'text/plain' })],
      },
    })

    expect(screen.queryByAltText(/aperçu du produit/i)).not.toBeInTheDocument()
    expect(screen.getByText(/utilisez une image jpg/i)).toBeInTheDocument()
  })

  it('matches the backend 100-character brand limit', () => {
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>
    )

    expect(screen.getByLabelText(/marque/i)).toHaveAttribute('maxlength', '100')
  })

  it('states the evidence language explicitly and updates it with the campaign', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>
    )

    expect(
      screen.getByText(/saisissez ces éléments en français/i)
    ).toBeInTheDocument()
    await user.selectOptions(
      screen.getByLabelText(/langue de la campagne/i),
      'en'
    )
    expect(
      screen.getByText(/saisissez ces éléments en anglais/i)
    ).toBeInTheDocument()
    expect(screen.getByText(/aucune traduction automatique/i)).toBeInTheDocument()
  })

  it('accepts the canonical unsigned 32-bit maximum seed', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>
    )

    const seed = screen.getByLabelText(/graine créative/i)
    await user.clear(seed)
    await user.type(seed, '4294967295')

    expect(seed).toHaveAttribute('max', '4294967295')
    expect(
      screen.queryByText(/utilisez un nombre entier/i)
    ).not.toBeInTheDocument()
  })
})
