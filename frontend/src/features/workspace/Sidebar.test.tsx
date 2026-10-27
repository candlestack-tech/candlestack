// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, MB, dashboardAnswer, model, modelsAnswer, session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const sidebar = () => screen.getByRole('navigation', { name: 'Workspace' });

describe('the sidebar', () => {
  it('counts the models beside Models', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([model(), model({ id: 'mdl_a2b3c4d5e6' })]) });
    await renderApp('/dashboard');
    const link = within(sidebar()).getByRole('link', { name: /^Models/ });
    await vi.waitFor(() => expect(link.textContent).toBe('Models2'));
    expect(link.lastElementChild?.textContent).toBe('2');
  });

  it('counts the backtests beside Backtests', async () => {
    stubApi({ ...session(ARSEN), ...dashboardAnswer({ counts: { all: 15, starred: 5, failed: 2 } }) });
    await renderApp('/models');
    const link = within(sidebar()).getByRole('link', { name: /^Backtests/ });
    await vi.waitFor(() => expect(link.textContent).toBe('Backtests15'));
    expect(link.lastElementChild?.textContent).toBe('15');
  });

  it('shows the storage the models take above the data note', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([model({ size_bytes: 25 * MB })]) });
    await renderApp('/backtests');
    const usage = await within(sidebar()).findByText('25 MB of 100 MB');
    const foot = usage.parentElement;
    expect([...(foot?.children ?? [])].map((child) => child.textContent)).toEqual([
      'Model storage',
      '',
      '25 MB of 100 MB',
      'Market data delayed 15 min',
    ]);
    // On one line: the figure never breaks, so nothing lands alone on a second line.
    expect(usage.className).toContain('whitespace-nowrap');
    const fill = foot?.children[1]?.firstElementChild as HTMLElement | null | undefined;
    expect(fill?.style.width).toBe('25%');
  });

  it('shows no numbers until the API has said them', async () => {
    stubApi({ ...session(ARSEN), 'GET /api/v1/models': { status: 503, body: { detail: 'Down.' } } });
    await renderApp('/dashboard');
    expect(within(sidebar()).getByRole('link', { name: 'Models' }).textContent).toBe('Models');
    expect(within(sidebar()).queryByText('Storage')).toBeNull();
    expect(within(sidebar()).getByText('Market data delayed 15 min')).toBeDefined();
  });
});
