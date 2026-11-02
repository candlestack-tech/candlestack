import { expect, test } from '@playwright/test';

test('the home page loads with its headline and the way to sign in', async ({ page }) => {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { level: 1, name: 'Backtest your models and compare every experiment' }),
  ).toBeVisible();
  await expect(page).toHaveTitle(/CandleStack/);
  await page.getByRole('link', { name: 'Sign in to browse' }).click();
  await expect(page).toHaveURL('/signin?next=%2Finstruments');
});

test('the sign-in page shows its buttons', async ({ page }) => {
  await page.goto('/signin');
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in to CandleStack' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue with GitHub' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeEnabled();
});
