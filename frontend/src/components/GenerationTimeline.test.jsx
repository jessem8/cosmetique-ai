import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import GenerationTimeline from './GenerationTimeline.jsx'

describe('GenerationTimeline', () => {
  it('uses only server-reported stages and never renders synthetic progress', () => {
    render(
      <GenerationTimeline
        status="processing"
        stage="background"
        completedStages={['analysis', 'extraction', 'art_direction']}
      />
    )

    expect(screen.getByText('Création du décor')).toHaveAttribute(
      'aria-current',
      'step'
    )
    expect(screen.getByText('Analyse du produit')).toHaveAttribute(
      'data-state',
      'complete'
    )
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/minute|seconde/i)).not.toBeInTheDocument()
  })
})
