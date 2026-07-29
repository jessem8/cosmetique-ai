import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import CandidateSelector from './CandidateSelector.jsx'

const candidates = [
  {
    id: 'candidate-1',
    score: 0.91,
    x: 0.1,
    y: 0.2,
    width: 0.3,
    height: 0.6,
  },
  {
    id: 'candidate-2',
    score: 0.78,
    x: 0.55,
    y: 0.15,
    width: 0.25,
    height: 0.65,
  },
]

describe('CandidateSelector', () => {
  it('lets a keyboard user choose a detected product and confirms its normalized box', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()

    render(
      <CandidateSelector
        imageUrl="blob:original"
        candidates={candidates}
        onConfirm={onConfirm}
      />
    )

    const second = screen.getByRole('radio', { name: /produit possible 2/i })
    second.focus()
    await user.keyboard(' ')
    await user.click(
      screen.getByRole('button', { name: /utiliser ce produit/i })
    )

    expect(onConfirm).toHaveBeenCalledWith({
      type: 'box',
      x: 0.55,
      y: 0.15,
      width: 0.25,
      height: 0.65,
    })
  })

  it('does not confirm before a candidate is selected', () => {
    render(
      <CandidateSelector
        imageUrl="blob:original"
        candidates={candidates}
        onConfirm={() => {}}
      />
    )

    expect(
      screen.getByRole('button', { name: /utiliser ce produit/i })
    ).toBeDisabled()
  })

  it('supports radio-group arrow navigation without a pointer', async () => {
    const user = userEvent.setup()
    render(
      <CandidateSelector
        imageUrl="blob:original"
        candidates={candidates}
        onConfirm={() => {}}
      />
    )

    const first = screen.getByRole('radio', { name: /produit possible 1/i })
    const second = screen.getByRole('radio', { name: /produit possible 2/i })
    first.focus()
    await user.keyboard('{ArrowRight}')

    expect(second).toHaveFocus()
    expect(second).toHaveAttribute('aria-checked', 'true')
  })
})
