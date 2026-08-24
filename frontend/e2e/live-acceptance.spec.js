import { test, expect } from '@playwright/test'

test('authenticated random dataset image reaches product extraction evidence', async ({ page }) => {
  test.setTimeout(1_500_000)
  const source = 'C:/gf_ai_task/.private_dataset/Stage_1_ouvrier/data/raw/product_photos/p165.jpg'
  const consoleErrors = []
  const requestErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('requestfailed', (request) => requestErrors.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || 'failed'}`))

  await page.goto('http://127.0.0.1/login', { waitUntil: 'networkidle' })
  await page.getByLabel('Adresse e-mail').fill(process.env.ACCEPTANCE_EMAIL)
  await page.getByRole('textbox', { name: 'Mot de passe' }).fill(process.env.ACCEPTANCE_PASSWORD)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await page.waitForURL('**/dashboard')

  await page.goto('http://127.0.0.1/new', { waitUntil: 'networkidle' })
  await page.locator('#atelier-product-image').setInputFiles(source)
  await page.getByLabel(/nom du produit/i).fill('Dataset p165')
  await page.getByRole('button', { name: /extraire le produit/i }).click()
  await expect(page.getByRole('heading', { name: 'Produit extrait' })).toBeVisible({ timeout: 180_000 })
  await expect(page.getByAltText('Produit extrait sur fond transparent')).toBeVisible()
  await page.screenshot({ path: '../reports/live_acceptance_20260823/02-extraction-browser.png', fullPage: true })

  await expect(page.getByRole('button', { name: /générer|décor|closerouter/i })).toHaveCount(0)
  expect(consoleErrors).toEqual([])
  expect(requestErrors).toEqual([])
})
