import { http, HttpResponse } from 'msw'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Login from './Login.jsx'
import { server } from '../test/server.js'
import { TEST_TOKEN } from '../test/token.js'

describe('login', () => {
  it('stores a validated token and restores the requested destination', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('*/api/v1/auth/login', () =>
        HttpResponse.json({
          access_token: TEST_TOKEN,
          token_type: 'bearer',
        })
      )
    )

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/login',
            state: { from: { pathname: '/new' } },
          },
        ]}
      >
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/new" element={<h1>Nouveau brief</h1>} />
        </Routes>
      </MemoryRouter>
    )

    await user.type(
      screen.getByRole('textbox', { name: /adresse e-mail/i }),
      'studio@example.com'
    )
    await user.type(screen.getByLabelText(/^mot de passe$/i), 'motdepasse123')
    await user.click(screen.getByRole('button', { name: /se connecter/i }))

    expect(
      await screen.findByRole('heading', { name: 'Nouveau brief' })
    ).toBeInTheDocument()
    expect(localStorage.getItem('cosmetique_ai_token')).toBe(
      TEST_TOKEN
    )
  })

  it('announces a safe API error', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('*/api/v1/auth/login', () =>
        HttpResponse.json(
          {
            error: {
              code: 'AUTH_REQUIRED',
              message: 'Adresse e-mail ou mot de passe incorrect.',
            },
          },
          { status: 401 }
        )
      )
    )

    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    )

    await user.type(
      screen.getByRole('textbox', { name: /adresse e-mail/i }),
      'studio@example.com'
    )
    await user.type(screen.getByLabelText(/^mot de passe$/i), 'incorrect')
    await user.click(screen.getByRole('button', { name: /se connecter/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Adresse e-mail ou mot de passe incorrect.'
    )
  })
})
