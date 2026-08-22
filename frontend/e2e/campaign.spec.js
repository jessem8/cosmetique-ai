import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const encode = (value) =>
  Buffer.from(JSON.stringify(value)).toString('base64url')

const validToken = `${encode({ alg: 'none', typ: 'JWT' })}.${encode({
  sub: '11111111-1111-4111-8111-111111111111',
  exp: 4_102_444_800,
})}.e2e-signature`

const expiredToken = `${encode({ alg: 'none', typ: 'JWT' })}.${encode({
  sub: '11111111-1111-4111-8111-111111111111',
  exp: 1,
})}.e2e-signature`

const product = {
  name: 'Sérum perle',
  brand: 'Maison Lune',
  category: 'soin_visage',
}

const imageSvg = (label = 'CA') => `
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 700">
    <rect width="900" height="700" fill="#edf0f3"/>
    <rect x="300" y="105" width="300" height="500" rx="36" fill="#fff" stroke="#8f1644" stroke-width="8"/>
    <text x="450" y="350" text-anchor="middle" font-family="serif" font-size="72" fill="#191c21">${label}</text>
  </svg>
`

const platformCopy = (text, evidenceId) => ({
  text,
  hashtags: ['#MaisonLune'],
  claims: evidenceId
    ? [{ evidence_id: evidenceId, rendered_text: text }]
    : [],
})

const completedGeneration = ({
  id = 'generation-done',
  language = 'fr',
} = {}) => ({
  id,
  product_id: 'product-1',
  product,
  source_generation_id: null,
  status: 'done',
  stage: 'packaging',
  completed_stages: [
    'analysis',
    'extraction',
    'art_direction',
    'background',
    'composition',
    'copy',
    'export',
    'packaging',
  ],
  language,
  seed: 42,
  attempt_count: 1,
  created_at: '2026-07-28T12:00:00Z',
  updated_at: '2026-07-28T12:05:00Z',
  error: null,
  ambiguity: null,
  copy: {
    language,
    instagram: platformCopy(
      language === 'en' ? 'Hydrates skin.' : 'Hydrate la peau.',
      'benefit-1'
    ),
    facebook: platformCopy(
      language === 'en' ? 'Hydrates skin.' : 'Hydrate la peau.',
      'benefit-1'
    ),
    linkedin: platformCopy(
      language === 'en' ? 'Hydrates skin.' : 'Hydrate la peau.',
      'benefit-1'
    ),
  },
  artifacts: [],
})

const authenticate = async (page, token = validToken) => {
  await page.addInitScript(
    ({ sessionToken }) => {
      localStorage.setItem('cosmetique_ai_token', sessionToken)
      localStorage.setItem('cosmetique_ai_email', 'studio@example.com')
    },
    { sessionToken: token }
  )
}

const fulfillJson = (route, body, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

const fulfillImage = (route, label) =>
  route.fulfill({
    status: 200,
    contentType: 'image/svg+xml',
    body: imageSvg(label),
  })

const expectNoHorizontalOverflow = async (page) => {
  const overflows = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  )
  expect(overflows).toBe(false)
}

const expectNoAxeViolations = async (page, state) => {
  const results = await new AxeBuilder({ page })
    .withTags([
      'wcag2a',
      'wcag2aa',
      'wcag21a',
      'wcag21aa',
      'wcag22aa',
    ])
    .options({
      rules: {
        'target-size': { enabled: true },
      },
    })
    .analyze()
  const summary = results.violations
    .map(
      (violation) =>
        `${violation.id}: ${violation.nodes
          .map((node) => node.target.join(' '))
          .join(', ')}`
    )
    .join('\n')
  expect(results.violations, `${state}\n${summary}`).toEqual([])
}

const relativeLuminance = ([red, green, blue]) => {
  const channels = [red, green, blue].map((value) => {
    const channel = value / 255
    return channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

const parseRgb = (value) =>
  value
    .match(/\d+(?:\.\d+)?/g)
    .slice(0, 3)
    .map(Number)

const contrastRatio = (foreground, background) => {
  const first = relativeLuminance(parseRgb(foreground))
  const second = relativeLuminance(parseRgb(background))
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

const expectSmallTextContrast = async (page, selector, state) => {
  const samples = await page.locator(selector).evaluateAll((elements) =>
    elements
      .filter((element) => {
        const style = getComputedStyle(element)
        const bounds = element.getBoundingClientRect()
        return (
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          bounds.width > 0 &&
          bounds.height > 0
        )
      })
      .map((element) => {
        let backgroundNode = element
        let background = 'rgb(255, 255, 255)'
        while (backgroundNode) {
          const candidate = getComputedStyle(backgroundNode).backgroundColor
          if (
            candidate !== 'transparent' &&
            candidate !== 'rgba(0, 0, 0, 0)'
          ) {
            background = candidate
            break
          }
          backgroundNode = backgroundNode.parentElement
        }
        return {
          text: element.textContent.trim().slice(0, 80),
          foreground: getComputedStyle(element).color,
          background,
        }
      })
  )
  expect(samples.length, `${state} did not resolve contrast samples`).toBeGreaterThan(0)
  samples.forEach((sample) => {
    expect(
      contrastRatio(sample.foreground, sample.background),
      `${state}: "${sample.text}" uses ${sample.foreground} on ${sample.background}`
    ).toBeGreaterThanOrEqual(4.5)
  })
}

test('history opens a French result, switches platform copy, evidence, and ZIP', async ({
  page,
}, testInfo) => {
  await authenticate(page)
  const done = completedGeneration()
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'GET' && path === '/api/v1/generations') {
      return fulfillJson(route, {
        items: [
          done,
          {
            ...done,
            id: 'generation-error',
            product: {
              name: 'Crème minérale',
              brand: 'Maison Lune',
              category: 'soin_visage',
            },
            status: 'error',
            error: {
              code: 'AI_RUNTIME_LOST',
              message: 'La session IA a été interrompue.',
            },
          },
        ],
        next_cursor: null,
      })
    }
    if (request.method() === 'GET' && path === '/api/v1/generations/generation-done') {
      return fulfillJson(route, done)
    }
    if (path === '/api/v1/products/product-1/image') {
      return fulfillImage(route, 'ORIGINAL')
    }
    if (path.includes('/artifacts/')) return fulfillImage(route, 'FINAL')
    if (path.endsWith('/bundle')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/zip',
        body: Buffer.from('validated-zip'),
        headers: {
          'content-disposition':
            'attachment; filename="cosmetique-ai-generation-done.zip"',
        },
      })
    }
    return route.abort()
  })

  await page.goto('/dashboard')
  await expect(page.getByRole('heading', { name: 'Campagnes' })).toBeVisible()
  await expect(page.getByText('Sérum perle')).toBeVisible()
  await expect(page.getByText('Session IA interrompue')).toBeVisible()
  await expectNoAxeViolations(page, 'Bibliothèque de campagnes')
  await expectSmallTextContrast(
    page,
    '.campaign-row__meta dt, .campaign-row__meta dd, .campaign-row__error',
    'Métadonnées de la bibliothèque'
  )
  await expectNoHorizontalOverflow(page)
  await page.screenshot({
    path: testInfo.outputPath('history.png'),
  })

  await page
    .getByRole('link', { name: 'Ouvrir la campagne Sérum perle' })
    .click()
  await expect(page.getByRole('tab', { name: 'Instagram' })).toBeVisible()
  await page.getByRole('tab', { name: 'Facebook' }).click()
  await expect(
    page
      .locator('.copy-block > p')
      .filter({ hasText: 'Hydrate la peau.' })
  ).toBeVisible()
  await expectNoAxeViolations(page, 'Résultat français')
  await expectSmallTextContrast(
    page,
    '.copy-panel .eyebrow, .copy-block > span, .copy-block > p, .copy-panel__proof p',
    'Texte du résultat français'
  )

  await page.getByRole('button', { name: 'Voir les preuves' }).click()
  await expect(
    page.getByRole('dialog', { name: 'Preuves de composition' })
  ).toBeVisible()
  await expectNoAxeViolations(page, 'Tiroir de preuves')
  await page.screenshot({
    path: testInfo.outputPath('evidence.png'),
  })
  await page.getByRole('button', { name: 'Fermer les preuves' }).click()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', {
    name: 'Télécharger le dossier ZIP',
  }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toContain('cosmetique-ai-generation-done')
})

test('an English campaign runs from brief to completed result', async ({
  page,
}, testInfo) => {
  await authenticate(page)
  const done = completedGeneration({ id: 'generation-new', language: 'en' })
  let generationBody
  let generationKey
  let uploadContentType

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST' && path === '/api/v1/products') {
      uploadContentType = request.headers()['content-type']
      return fulfillJson(route, { id: 'product-1' }, 201)
    }
    if (
      request.method() === 'POST' &&
      path === '/api/v1/products/product-1/generations'
    ) {
      generationBody = request.postDataJSON()
      generationKey = request.headers()['idempotency-key']
      return fulfillJson(route, { id: 'generation-new', status: 'pending' }, 202)
    }
    if (request.method() === 'GET' && path === '/api/v1/generations/generation-new') {
      return fulfillJson(route, done)
    }
    if (path === '/api/v1/products/product-1/image') {
      return fulfillImage(route, 'ORIGINAL')
    }
    if (path.includes('/artifacts/')) return fulfillImage(route, 'EN')
    return route.abort()
  })

  await page.goto('/new')
  await page.getByLabel('Photo du produit').setInputFiles({
    name: 'serum.svg',
    mimeType: 'image/png',
    buffer: Buffer.from(imageSvg('PRODUCT')),
  })
  await page.getByLabel('Nom du produit').fill('Sérum perle')
  await page.getByLabel('Langue de la campagne').selectOption('en')
  await page.getByLabel('Bénéfices vérifiés').fill('Hydrates skin')
  await expect(page.getByText(/saisissez ces éléments en anglais/i)).toBeVisible()
  await expectNoAxeViolations(page, 'Brief anglais renseigné')
  await page.getByRole('button', { name: 'Lancer la campagne' }).click()

  await expect(page).toHaveURL(/\/result\/generation-new$/)
  await expect(page.getByText('Texte anglais')).toBeVisible()
  const englishProse = page
    .locator('.copy-block > p')
    .filter({ hasText: 'Hydrates skin.' })
  await expect(englishProse).toBeVisible()
  await expect(englishProse).toHaveAttribute('lang', 'en')
  await expect(
    page.locator('.copy-claims [lang="en"]').filter({ hasText: 'Hydrates skin.' })
  ).toBeVisible()
  const copyButton = page.getByRole('button', {
    name: 'Copier le texte Instagram',
  })
  await expect(copyButton).toHaveText('Copier')
  expect(
    await copyButton.evaluate((element) => getComputedStyle(element).whiteSpace)
  ).toBe('nowrap')
  expect(generationBody).toMatchObject({
    language: 'en',
    benefits: ['Hydrates skin'],
  })
  expect(generationKey).toMatch(/^campaign-/)
  expect(uploadContentType).toMatch(/^multipart\/form-data;\s*boundary=/)
  await expectNoAxeViolations(page, 'Résultat anglais')
  await expectSmallTextContrast(
    page,
    '.copy-panel .eyebrow, .copy-block > span, .copy-block > p, .copy-panel__proof p',
    'Texte du résultat anglais'
  )
  await expectNoHorizontalOverflow(page)
  await page.screenshot({
    path: testInfo.outputPath('english-result.png'),
    fullPage: true,
  })
})

test('ambiguous detection requires a box and creates a linked campaign', async ({
  page,
}, testInfo) => {
  await authenticate(page)
  let selectionBody
  let selectionKey
  const ambiguous = {
    id: 'generation-ambiguous',
    product_id: 'product-1',
    product,
    status: 'error',
    stage: 'analysis',
    completed_stages: [],
    language: 'fr',
    seed: 42,
    error: {
      code: 'TARGET_AMBIGUOUS',
      message: 'Plusieurs produits possibles ont été détectés.',
    },
    ambiguity: {
      original_url: '/api/v1/products/product-1/image',
      candidates: [
        {
          id: 'candidate-1',
          score: 0.91,
          type: 'box',
          x: 0.08,
          y: 0.12,
          width: 0.35,
          height: 0.72,
        },
        {
          id: 'candidate-2',
          score: 0.86,
          type: 'box',
          x: 0.55,
          y: 0.18,
          width: 0.32,
          height: 0.65,
        },
      ],
    },
  }

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (
      request.method() === 'GET' &&
      path === '/api/v1/generations/generation-ambiguous'
    ) {
      return fulfillJson(route, ambiguous)
    }
    if (path === '/api/v1/products/product-1/image') {
      return fulfillImage(route, '1    2')
    }
    if (
      request.method() === 'POST' &&
      path === '/api/v1/products/product-1/generations'
    ) {
      selectionBody = request.postDataJSON()
      selectionKey = request.headers()['idempotency-key']
      return fulfillJson(route, { id: 'generation-selected', status: 'pending' }, 202)
    }
    if (
      request.method() === 'GET' &&
      path === '/api/v1/generations/generation-selected'
    ) {
      return fulfillJson(route, {
        id: 'generation-selected',
        product_id: 'product-1',
        product,
        status: 'processing',
        stage: 'art_direction',
        completed_stages: ['analysis', 'extraction'],
        language: 'fr',
        seed: 42,
      })
    }
    return route.abort()
  })

  await page.goto('/generations/generation-ambiguous')
  await expect(
    page.getByRole('heading', { name: 'Confirmez le bon produit.' })
  ).toBeVisible()
  await expectNoAxeViolations(page, 'Sélection du produit ambigu')
  await page.getByRole('radio', { name: 'Produit possible 2' }).click()
  await page.screenshot({
    path: testInfo.outputPath('candidate-selection.png'),
  })
  await page.getByRole('button', { name: 'Utiliser ce produit' }).click()

  await expect(page).toHaveURL(/\/generations\/generation-selected$/)
  await expect(page.getByText('Direction artistique')).toHaveAttribute(
    'aria-current',
    'step'
  )
  await expectNoAxeViolations(page, 'Progression de campagne')
  expect(selectionKey).toMatch(/^campaign-/)
  expect(selectionBody).toMatchObject({
    source_generation_id: 'generation-ambiguous',
    target_hint: {
      type: 'box',
      x: 0.55,
      y: 0.18,
      width: 0.32,
      height: 0.65,
    },
  })
})

test('runtime loss is an explicit terminal failure', async ({ page }) => {
  await authenticate(page)
  await page.route('**/api/v1/generations/generation-failed', (route) =>
    fulfillJson(route, {
      id: 'generation-failed',
      product_id: 'product-1',
      product,
      status: 'error',
      stage: 'background',
      completed_stages: ['analysis', 'extraction', 'art_direction'],
      language: 'fr',
      seed: 42,
      error: {
        code: 'AI_RUNTIME_LOST',
        message: 'La session IA a été interrompue.',
      },
    })
  )

  await page.goto('/generations/generation-failed')
  await expect(
    page.getByRole('heading', { name: 'Session IA interrompue' })
  ).toBeVisible()
  await expect(
    page.getByText(/aucun résultat dégradé n’a été enregistré/i)
  ).toBeVisible()
  await expect(page.getByText(/campagne terminée/i)).toHaveCount(0)
  await expectNoAxeViolations(page, 'Échec explicite de session IA')
})

test('an expired session returns to the login route', async ({ page }) => {
  await authenticate(page, expiredToken)
  await page.goto('/dashboard')

  await expect(page).toHaveURL(/\/login$/)
  await expect(
    page.getByRole('heading', { name: 'Accéder au studio' })
  ).toBeVisible()
  const storedToken = await page.evaluate(() =>
    localStorage.getItem('cosmetique_ai_token')
  )
  expect(storedToken).toBeNull()
  await expectNoAxeViolations(page, 'Authentification après expiration')
})

const mockStudioV2 = async (page) => {
  let generationId = null
  await page.route('**/api/v1/studio/v2/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (request.method() === 'GET' && path.endsWith('/provider-profiles')) {
      return fulfillJson(route, {
        providers: [
          {
            id: 'studio-safe-v1',
            name: 'Studio Safe',
            model: 'Product-preserve 1.4',
            capabilities: ['mask-preservation', 'scene-generation', 'qa-provenance'],
            costPerVariant: 0.8,
            maxVariants: 4,
            supportsCustomDirection: true,
          },
        ],
      })
    }
    if (request.method() === 'POST' && path.endsWith('/product-locks')) {
      return fulfillJson(route, { id: 'lock-e2e-1', revision: 1, status: 'needs_review' }, 201)
    }
    if (request.method() === 'POST' && path.endsWith('/validate')) {
      return fulfillJson(route, { id: 'lock-e2e-1', revision: 1, status: 'validated' })
    }
    if (request.method() === 'POST' && path.endsWith('/refine')) {
      return fulfillJson(route, { id: 'lock-e2e-1', revision: 2, status: 'needs_review' })
    }
    if (request.method() === 'POST' && path.endsWith('/reject')) {
      return fulfillJson(route, { id: 'lock-e2e-1', revision: 2, status: 'rejected' })
    }
    if (request.method() === 'POST' && path.endsWith('/generations')) {
      generationId = 'generation-e2e-v2'
      return fulfillJson(route, { id: generationId, status: 'queued', stage: 'preparing' }, 202)
    }
    if (
      request.method() === 'GET' &&
      path.includes('/generations/') &&
      !path.endsWith('/variants') &&
      !path.endsWith('/export')
    ) {
      return fulfillJson(route, { id: generationId, status: 'done', stage: 'qa', message: 'Fixture QA complete.' })
    }
    if (request.method() === 'GET' && path.endsWith('/variants')) {
      return fulfillJson(route, {
        variants: [
          {
            id: 'variant-e2e-1',
            label: 'Soft daylight',
            image_url: '/studio/fixture-scene.svg',
            qa: { status: 'pass', score: 0.98, failures: [] },
            provenance: { provider: 'Studio Safe', model: 'Product-preserve 1.4', seed: 2808, cost: 0.8 },
          },
          {
            id: 'variant-e2e-2',
            label: 'Cool counter',
            image_url: '/studio/fixture-scene-alt.svg',
            qa: { status: 'review', score: 0.91, failures: ['Lower edge confidence below review threshold'] },
            provenance: { provider: 'Studio Safe', model: 'Product-preserve 1.4', seed: 2809, cost: 0.8 },
          },
        ],
      })
    }
    if (request.method() === 'POST' && path.endsWith('/cancel')) {
      return fulfillJson(route, { id: generationId, status: 'canceled' })
    }
    if (request.method() === 'GET' && path.endsWith('/export')) {
      return route.fulfill({ status: 200, contentType: 'application/zip', body: Buffer.from('validated fixture zip') })
    }
    return route.abort()
  })
}

test('Campaign Studio V2 desktop flow keeps lock, generation, compare, and export truthful', async ({ page }) => {
  await authenticate(page)
  await mockStudioV2(page)
  await page.goto('/studio-v2')
  await expect(page.getByRole('heading', { name: 'Product source' })).toBeVisible()
  await expectNoAxeViolations(page, 'Campaign Studio V2 source')

  await page.getByRole('button', { name: /create product lock/i }).click()
  await expect(page.getByRole('heading', { name: 'Tune the product boundary' })).toBeVisible()
  await page.getByRole('button', { name: /validate lock/i }).click()
  await expect(page.getByRole('heading', { name: 'Give the scene a point of view' })).toBeVisible()

  await page.getByRole('button', { name: /^generate/i }).click()
  await page.getByRole('button', { name: /start new generation/i }).click()
  await expect(page.getByText('Ready for review')).toBeVisible()
  await page.getByRole('button', { name: /^compare/i }).click()
  await expect(page.getByRole('heading', { name: 'Review variants before delivery' })).toBeVisible()
  await expect(page.getByText(/local fixtures are not model output/i)).toBeVisible()
  await page.getByRole('button', { name: /^export/i }).click()
  await expect(page.getByRole('heading', { name: 'Package the approved campaign' })).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: /download validated zip/i }).click()
  await expect((await download).suggestedFilename()).toMatch(/generation-e2e-v2\.zip/)
  await expectNoHorizontalOverflow(page)
})

test('Campaign Studio V2 mobile flow remains a bounded task sequence', async ({ page }) => {
  await authenticate(page)
  await mockStudioV2(page)
  await page.goto('/studio-v2')
  await expect(page.getByRole('button', { name: /continue/i })).toBeVisible()
  await page.getByRole('button', { name: /create product lock/i }).click()
  await expect(page.getByRole('heading', { name: 'Tune the product boundary' })).toBeVisible()
  await expect(page.getByRole('button', { name: /add positive point/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /continue/i })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectNoAxeViolations(page, 'Campaign Studio V2 mobile lock')
})
