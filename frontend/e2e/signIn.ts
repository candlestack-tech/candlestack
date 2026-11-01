import type { Page } from '@playwright/test';

// Signs the browser in as the local stack's dev user (POST /api/v1/auth/dev, only on APP_ENV
// local and test). The request goes through the dev server, so the session cookie lands on the
// app's origin, and it carries that origin, which the API checks.
export async function signIn(page: Page, baseURL: string | undefined) {
  const response = await page.request.post('/api/v1/auth/dev', { headers: { Origin: baseURL ?? '' } });
  if (response.status() !== 204) {
    throw new Error(`The dev sign-in answered ${response.status()}: is the local stack up (docker compose up)?`);
  }
}
