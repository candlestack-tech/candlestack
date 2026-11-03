// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, backtest, backtestsAnswer, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, viewerIn } from '@/test/app';

// The Filters panel of the Backtests page: Show and Model.

// #12 (Bright Vega), and #10 (Crimson Nova), starred.
const TWELVE = backtest();
const TEN = backtest({
  id: 'bt_q7r8s9t2u3',
  number: 10,
  name: 'Crimson Nova',
  generated_name: 'Crimson Nova',
  starred: true,
});
const COUNTS = { all: 3, starred: 1, failed: 1, public: 0 };
const MODELS = [{ name: 'blesk-novol-8.keras', framework: 'keras', count: 3 }];
const PAGE = { backtests: [TWELVE, TEN], counts: COUNTS, models: MODELS };

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
  viewerIn('America/New_York');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const asked = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith('/api/v1/backtests'));

describe('the Filters of the Backtests page', () => {
  it('filters by Show and Model, keeping the panel open', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      ...backtestsAnswer(PAGE),
      ...backtestsAnswer({ ...PAGE, backtests: [TEN], total: 1 }, '?show=starred'),
      ...backtestsAnswer({ ...PAGE, backtests: [TEN], total: 1 }, '?show=starred&model=blesk-novol-8.keras'),
    });
    await renderApp('/backtests');
    await screen.findByText('#12');
    await userEvent.click(screen.getByRole('button', { name: 'Filters: All backtests' }));
    const panel = screen.getByRole('dialog', { name: 'Filters' });
    expect(
      within(panel)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['All backtests3', 'Starred1', 'Public only0', 'Failed1', 'All models', 'blesk-novol-8.keras3']);
    await userEvent.click(within(panel).getByRole('option', { name: /^Starred/ }));
    await screen.findByRole('button', { name: 'Filters: Starred' });
    await userEvent.click(within(panel).getByRole('option', { name: /blesk-novol-8/ }));
    await screen.findByRole('button', { name: 'Filters: Starred · blesk-novol-8.keras' });
    expect(screen.getByRole('dialog', { name: 'Filters' })).toBeDefined();
    expect(asked(fetch).at(-1)).toBe('/api/v1/backtests?show=starred&model=blesk-novol-8.keras');
  });

  it('pops the Filters panel open from its corner and closes it at once, 8 px under each heading', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    const button = screen.getByRole('button', { name: 'Filters: All backtests' });
    await userEvent.click(button);
    const panel = screen.getByRole('dialog', { name: 'Filters' });
    expect(panel.className.split(' ')).toEqual(
      expect.arrayContaining([
        'origin-top-left',
        'data-[state=open]:animate-fx-pop!',
        'data-[state=closed]:animate-none!',
      ]),
    );
    for (const name of ['Show', 'Model']) {
      const list = within(panel).getByRole('listbox', { name });
      const heading = document.getElementById(list.getAttribute('aria-labelledby') ?? '');
      expect(heading?.className.split(' ')).toContain('mb-2');
      expect(heading?.className).not.toMatch(/mb-1/);
    }
    // Nothing plays on closing: the panel is gone right after Escape, the focus back on the button.
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Filters' })).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it('ends a model name too long for the Filters panel in an ellipsis, its count still beside it', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    await userEvent.click(screen.getByRole('button', { name: 'Filters: All backtests' }));
    const option = within(screen.getByRole('dialog', { name: 'Filters' })).getByRole('option', { name: /blesk/ });
    const name = within(option).getByText('blesk-novol-8.keras');
    expect(name.className.split(' ')).toEqual(expect.arrayContaining(['min-w-0', 'truncate']));
    // The mark and the name sit in a row no wider than the entry's label, which gives way first.
    expect(name.parentElement?.className.split(' ')).toContain('max-w-full');
    expect(name.parentElement?.parentElement?.className.split(' ')).toEqual(
      expect.arrayContaining(['min-w-0', 'truncate']),
    );
    expect(within(option).getByText('3').className.split(' ')).toContain('shrink-0');
  });

  it('lights an entry of the Filters panel under the pointer, and where the focus is only once keys are used', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), ...backtestsAnswer(PAGE) });
    await renderApp('/backtests');
    await screen.findByText('#12');
    await userEvent.click(screen.getByRole('button', { name: 'Filters: All backtests' }));
    const panel = screen.getByRole('dialog', { name: 'Filters' });
    const first = within(panel).getAllByRole('option')[0]!;
    expect(document.activeElement).toBe(first);
    expect(panel.hasAttribute('data-keys')).toBe(false);
    expect(first.className.split(' ')).toEqual(
      expect.arrayContaining(['hover:bg-accent', 'in-data-keys:focus:bg-accent']),
    );
    expect(first.className).not.toMatch(/(^| )focus:bg-accent/);
    expect(first.querySelector('svg')?.getAttribute('class')).toMatch(/(^| )size-4( |$)/);
    await userEvent.keyboard('{ArrowDown}');
    expect(panel.getAttribute('data-keys')).toBe('true');
    expect(document.activeElement).toBe(within(panel).getAllByRole('option')[1]);
    await userEvent.keyboard('{Escape}');
    // Opened from the keyboard, the panel says so at once.
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('dialog', { name: 'Filters' }).getAttribute('data-keys')).toBe('true');
  });
});
