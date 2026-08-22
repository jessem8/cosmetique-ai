import { http, HttpResponse } from 'msw'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import StudioWorkspace from './StudioWorkspace.jsx'
import DirectionPanel from './DirectionPanel.jsx'
import { server } from '../test/server.js'

const renderWorkspace = () => render(<MemoryRouter initialEntries={['/new']}><StudioWorkspace /></MemoryRouter>)
const DirectionHarness = () => {
  const [direction, setDirection] = useState({ mode: 'automatic', prompt: '', audience: '', placement: 'square', seed: 1 })
  return <DirectionPanel direction={direction} source={{ category: 'skincare', name: 'Sérum perle' }} lockReady={false} busy={false} onChange={(patch) => setDirection((current) => ({ ...current, ...patch }))} onPreview={() => {}} onContinue={() => {}} />
}

describe('French Campaign Studio workspace', () => {
  it('opens the real source stage with truthful engine and batch states', async () => {
    server.use(
      http.get('*/api/v1/studio/v2/engine/status', () => HttpResponse.json({ status: 'ready', gpu: 'Tesla T4', runtime: 'colab-1', models: ['product-lock', 'background'] })),
      http.get('*/api/v1/studio/v2/provider-profiles', () => HttpResponse.json([])),
      http.get('*/api/v1/studio/v2/batches', () => HttpResponse.json({ items: [] })),
    )

    renderWorkspace()

    expect(await screen.findByRole('heading', { name: /une image produit/i })).toBeInTheDocument()
    expect(screen.getByText('Colab est prêt')).toBeInTheDocument()
    expect(screen.getByText('Aucun traitement enregistré.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /créer le product lock/i })).toBeDisabled()
  })

  it('keeps automatic and custom direction as explicit French modes', async () => {
    const user = userEvent.setup()
    render(<DirectionHarness />)
    expect(screen.getByText('Product Lock requis')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /mon prompt/i }))
    expect(screen.getByRole('tab', { name: /mon prompt/i })).toHaveAttribute('aria-selected', 'true')
  })
})
