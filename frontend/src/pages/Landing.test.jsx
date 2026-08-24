import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Landing from './Landing.jsx'

describe('public extraction landing', () => {
  it('describes the supported extraction workflow without generation claims', () => {
    render(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>
    )

    expect(screen.getByRole('heading', { name: /le produit reste vrai/i })).toBeInTheDocument()
    expect(screen.getByText('Masque binaire')).toBeInTheDocument()
    expect(screen.getByText('Cutout transparent')).toBeInTheDocument()
    expect(screen.getByText(/aucune génération de décor/i)).toBeInTheDocument()
    expect(screen.queryByText(/rendu accepté apparaîtra/i)).not.toBeInTheDocument()
  })

  it('links the public entry points to authentication', () => {
    render(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>
    )

    expect(screen.getByRole('link', { name: /commencer une extraction/i })).toHaveAttribute('href', '/register')
    expect(screen.getByRole('link', { name: /entrer dans l’extraction/i })).toHaveAttribute('href', '/login')
  })
})
