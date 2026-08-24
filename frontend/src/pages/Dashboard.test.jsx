import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Dashboard from './Dashboard.jsx'

describe('extraction dashboard', () => {
  it('presents only the supported product extraction workflow', () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )

    expect(screen.getByRole('heading', { name: 'Extractions' })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /extraire un produit/i })).toHaveLength(1)
    expect(screen.getByText('Importer la source')).toBeInTheDocument()
    expect(screen.getByText('Extraire le produit')).toBeInTheDocument()
    expect(screen.getByText('Vérifier la preuve')).toBeInTheDocument()
    expect(screen.queryByText(/génération/i)).not.toBeInTheDocument()
  })

  it('routes both extraction entry points to the real Studio', () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )

    expect(screen.getAllByRole('link', { name: /extraire un produit/i }).every((link) => link.getAttribute('href') === '/new')).toBe(true)
    expect(screen.getByRole('link', { name: /ouvrir l’extraction/i })).toHaveAttribute('href', '/new')
  })
})
