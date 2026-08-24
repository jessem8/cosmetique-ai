import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App.jsx'
import { setSession } from './auth/session.js'
import { TEST_TOKEN } from './test/token.js'

describe('application shell', () => {
  it('rejects unusable stored token values', async () => {
    localStorage.setItem('cosmetique_ai_token', 'undefined')

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <App />
      </MemoryRouter>
    )

    expect(
      await screen.findByRole('heading', { name: /accéder au studio/i })
    ).toBeInTheDocument()
  })

  it('provides French navigation, a skip link, and a main landmark', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <App />
      </MemoryRouter>
    )

    const navigation = await screen.findByRole('navigation', {
      name: /navigation principale/i,
    })
    expect(navigation).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /aller au contenu/i })).toHaveAttribute(
      'href',
      '#main-content'
    )
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content')
    expect(screen.getByRole('link', { name: 'Extractions' })).toBeInTheDocument()
    expect(
      within(navigation)
        .getAllByRole('link')
        .filter((link) => link.getAttribute('href') === '/new')
    ).toHaveLength(1)
  })

  it('renders a helpful not-found page instead of hiding broken routes', async () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })

    render(
      <MemoryRouter initialEntries={['/lien-introuvable']}>
        <App />
      </MemoryRouter>
    )

    expect(
      await screen.findByRole('heading', { name: /page introuvable/i })
    ).toBeInTheDocument()
  })
})
