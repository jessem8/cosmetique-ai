import { http, HttpResponse } from 'msw'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'vitest-axe'
import { describe, expect, it } from 'vitest'
import App from './App.jsx'
import { setSession } from './auth/session.js'
import { server } from './test/server.js'
import { TEST_TOKEN } from './test/token.js'

const authenticate = () =>
  setSession({ token: TEST_TOKEN, email: 'studio@example.com' })

const renderApp = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  )

const expectNoAxeViolations = async (container) => {
  const results = await axe(container, {
    // jsdom has no layout canvas. Playwright runs real color-contrast checks
    // across every configured desktop, tablet, and mobile project.
    rules: { 'color-contrast': { enabled: false } },
  })
  expect(results.violations).toEqual([])
}

describe('supported route accessibility', () => {
  it('has no detectable axe violations on authentication', async () => {
    const { container } = renderApp('/login')

    await screen.findByRole('heading', { name: /accéder au studio/i })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations on the public extraction landing', async () => {
    const { container } = renderApp('/')

    await screen.findByRole('heading', { name: /le produit reste vrai/i })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations in the extraction dashboard', async () => {
    authenticate()
    const { container } = renderApp('/dashboard')

    await screen.findByRole('heading', { name: 'Extractions' })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations in the extraction workspace', async () => {
    authenticate()
    server.use(
      http.get(
        '*/api/v1/studio/v2/engine/status',
        () =>
          HttpResponse.json({
            status: 'ready',
            ready: true,
            gpu: 'CPU U2Net',
            engine_mode: 'cpu-u2net',
            models: ['U2Net ONNX (CPU)'],
          })
      )
    )
    const { container } = renderApp('/new')

    await screen.findByRole('heading', { name: /produit, isolé proprement/i })
    await expectNoAxeViolations(container)
  })

  it('has no detectable axe violations on the authenticated not-found route', async () => {
    authenticate()
    const { container } = renderApp('/lien-introuvable')

    await screen.findByRole('heading', { name: /page introuvable/i })
    await expectNoAxeViolations(container)
  })
})
