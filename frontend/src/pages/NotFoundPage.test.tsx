// @vitest-environment jsdom
import { cleanup, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ADA, session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('NotFoundPage', () => {
  // routeTree.test.tsx checks which addresses show it; this checks what the page says and does.
  it('says there is nothing at the address, in the title too', async () => {
    stubApi(session(null));
    await renderApp('/nope');
    expect(screen.getByText('There is nothing at this address.')).toBeDefined();
    expect(document.title).toBe('Page not found · CandleStack');
  });

  it('leads home, not to the instrument list, signed in or not', async () => {
    for (const user of [null, ADA]) {
      stubApi(session(user));
      const { router } = await renderApp('/instruments/nope');
      await userEvent.setup().click(screen.getByRole('link', { name: 'Back to home' }));
      await vi.waitFor(() => expect(router.state.location.pathname).toBe('/'));
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(
        'Backtest your models and compare every experiment',
      );
      cleanup();
    }
  });
});
