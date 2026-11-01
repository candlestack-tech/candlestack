import { defineConfig, devices } from '@playwright/test';

// Smoke tests of the public pages in a real browser, against the local stack: the backend from
// `docker compose up` at the repository root, behind the dev server, which `npm run e2e` starts
// pointed at it. See README.md, End-to-end tests.
const APP = 'http://localhost:5173';

export default defineConfig({
  testDir: 'e2e',
  testMatch: '*.e2e.ts',
  // The candles of a cold instrument come from Binance through the local backend.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  // One worker: the tests share the backend's limit of 60 candle requests a minute.
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: APP, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev -- --strictPort',
    url: APP,
    env: { API_TARGET: process.env.API_TARGET ?? 'http://localhost:8000' },
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
