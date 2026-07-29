import { http, HttpResponse } from 'msw'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Dashboard from './Dashboard.jsx'
import { generations } from '../api/client.js'
import { setSession } from '../auth/session.js'
import { server } from '../test/server.js'
import { TEST_TOKEN } from '../test/token.js'

describe('campaign history', () => {
  it('renders the cursor history as campaigns, not latest product cards', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    server.use(
      http.get('*/api/v1/generations', () =>
        HttpResponse.json({
          items: [
            {
              id: 'generation-1',
              product_id: 'product-1',
              product: { name: 'Sérum perle', brand: 'Maison Lune' },
              language: 'fr',
              status: 'done',
              created_at: '2026-07-28T12:00:00Z',
            },
            {
              id: 'generation-2',
              product_id: 'product-1',
              product: { name: 'Sérum perle', brand: 'Maison Lune' },
              language: 'en',
              status: 'error',
              error: {
                code: 'AI_RUNTIME_LOST',
                message: 'La session GPU a été interrompue.',
              },
              created_at: '2026-07-28T13:00:00Z',
            },
          ],
          next_cursor: null,
        })
      )
    )

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )

    expect(await screen.findAllByText('Sérum perle')).toHaveLength(2)
    expect(screen.getByText('Français')).toBeInTheDocument()
    expect(screen.getByText('Anglais')).toBeInTheDocument()
    expect(screen.getByText('Session IA interrompue')).toBeInTheDocument()
  })

  it('provides a composed empty state with a campaign action', async () => {
    server.use(
      http.get('*/api/v1/generations', () =>
        HttpResponse.json({ items: [], next_cursor: null })
      )
    )

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )

    expect(
      await screen.findByRole('heading', { name: /votre première campagne/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /créer une campagne/i })
    ).toHaveAttribute('href', '/new')
  })

  it('aborts an in-flight cursor request when the history unmounts', async () => {
    const user = userEvent.setup()
    let loadMoreOptions
    vi.spyOn(generations, 'list')
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              id: 'generation-1',
              product: {
                name: 'Crème perle',
                brand: 'Maison Lune',
                category: 'soin_visage',
              },
              language: 'fr',
              status: 'processing',
              created_at: '2026-07-28T12:00:00Z',
            },
          ],
          next_cursor: 'cursor-2',
        },
      })
      .mockImplementationOnce((options) => {
        loadMoreOptions = options
        return new Promise(() => {})
      })

    const { unmount } = render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )

    await user.click(
      await screen.findByRole('button', { name: /afficher plus/i })
    )
    await waitFor(() => expect(loadMoreOptions).toBeDefined())
    expect(loadMoreOptions.signal).toBeInstanceOf(AbortSignal)
    expect(loadMoreOptions.signal.aborted).toBe(false)

    unmount()
    expect(loadMoreOptions.signal.aborted).toBe(true)
  })
})
