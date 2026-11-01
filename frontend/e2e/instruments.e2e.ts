import { expect, test, type Page } from '@playwright/test';

import { signIn } from './signIn.ts';

// The instrument list and an instrument's page are for signed-in accounts. Crypto only: the
// local stack has stocks only with Alpaca keys in .env.
test.beforeEach(async ({ page, baseURL }) => {
  await signIn(page, baseURL);
});

const rows = (page: Page) => page.getByRole('row').filter({ has: page.getByRole('cell') });

test('the instrument list searches, filters and pages', async ({ page }) => {
  await page.goto('/instruments');
  await expect(page.getByRole('heading', { level: 1, name: 'Instruments' })).toBeVisible();

  await page.getByRole('searchbox', { name: 'Search instruments by symbol or name' }).fill('usdt');
  await expect(page).toHaveURL(/[?&]q=usdt/);
  await expect(rows(page).first()).toContainText('USDT');

  await page.getByRole('button', { name: /^Filters:/ }).click();
  await page.getByRole('dialog', { name: 'Filters' }).getByRole('option', { name: 'Crypto' }).click();
  await expect(page).toHaveURL(/[?&]market=crypto/);
  await expect(page.getByRole('button', { name: /^Filters: Crypto/ })).toBeVisible();

  const pages = page.getByRole('navigation', { name: 'Pages' });
  await expect(pages).toContainText('Page 1');
  const first = await rows(page).first().textContent();
  await pages.getByRole('link', { name: 'Next' }).click();
  await expect(page).toHaveURL(/[?&]page=2/);
  await expect(pages).toContainText('Page 2');
  await expect(rows(page).first()).not.toHaveText(first ?? '');
});

test('an instrument page draws its chart', async ({ page }) => {
  await page.goto('/instrument/crypto:BTCUSDT?tf=1d');
  await expect(page).toHaveTitle('BTCUSDT 1d · CandleStack');
  await expect(page.getByRole('heading', { level: 1 })).not.toBeEmpty();
  const chart = page.getByRole('region', { name: 'BTCUSDT candles, 1d' });
  // lightweight-charts draws on canvases once the candles have arrived.
  await expect(chart.locator('canvas').first()).toBeVisible();
  await expect(chart.getByRole('button', { name: 'Try again' })).toHaveCount(0);
});
