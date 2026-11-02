import { expect, test, type Page } from '@playwright/test';

import { signIn } from './signIn.ts';

// The instrument list and an instrument's page are for signed-in accounts since #340, so these
// tests sign in as the dev user. The local stack lists crypto only, unless Alpaca keys are in .env.
test.beforeEach(async ({ page, baseURL }) => {
  await signIn(page, baseURL);
});

const rows = (page: Page) => page.getByRole('row').filter({ has: page.getByRole('cell') });
const pages = (page: Page) => page.getByRole('navigation', { name: 'Pages' });
// The first row's symbol, the link of its first cell.
const firstSymbol = (page: Page) => rows(page).first().getByRole('link').first();

test('the instrument search lists only what matches', async ({ page }) => {
  await page.goto('/instruments');
  await expect(page.getByRole('heading', { level: 1, name: 'Instruments' })).toBeVisible();

  await page.getByRole('searchbox', { name: 'Search instruments by symbol or name' }).fill('eth');
  await expect(page).toHaveURL(/[?&]q=eth/);
  await expect(rows(page).first()).toBeVisible();
  // The search matches the symbol or the name, both in the row.
  for (const row of await rows(page).all()) await expect(row).toContainText(/eth/i);
});

test('the type filter narrows the list and gives it back', async ({ page }) => {
  await page.goto('/instruments?q=usdt');
  await expect(rows(page).first()).toContainText('USDT');
  const all = await pages(page).textContent();

  // Stocks never match "usdt": with Alpaca keys the list is empty, without them stocks cannot be
  // searched. Either way none of the crypto pairs stays.
  await page.getByRole('button', { name: /^Filters:/ }).click();
  await page.getByRole('dialog', { name: 'Filters' }).getByRole('option', { name: 'Stocks', exact: true }).click();
  await expect(page).toHaveURL(/[?&]market=stock/);
  await expect(rows(page)).toHaveCount(0);

  // The filters stay open after a choice, so the next one is picked in the same dialog.
  await page.getByRole('dialog', { name: 'Filters' }).getByRole('option', { name: 'Crypto', exact: true }).click();
  await expect(page).toHaveURL(/[?&]market=crypto/);
  await expect(rows(page).first()).toContainText('USDT');
  await expect(pages(page)).toHaveText(all ?? '');
});

test('the instrument list pages', async ({ page }) => {
  await page.goto('/instruments');
  await expect(pages(page)).toContainText('Page 1');
  const first = await firstSymbol(page).textContent();
  await pages(page).getByRole('link', { name: 'Next' }).click();
  await expect(page).toHaveURL(/[?&]page=2/);
  await expect(pages(page)).toContainText('Page 2');
  await expect(firstSymbol(page)).not.toHaveText(first ?? '');
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
