// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ADA, MB, dashboardAnswer, model, modelsAnswer, session, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// The row of sections as a 360 px phone lays it out (#465): 326 px wide, 763 px of sections,
// Settings at its end from 650 px on.
function layOutAsPhone() {
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(763);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(326);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    return this.getAttribute('href') === '/settings' ? new DOMRect(650, 0, 100, 34) : new DOMRect(16, 0, 326, 42);
  });
  return vi.spyOn(Element.prototype, 'scrollLeft', 'set');
}

const sidebar = () => screen.getByRole('navigation', { name: 'Workspace' });

describe('the sidebar', () => {
  it('counts the models beside Models', async () => {
    stubApi({ ...session(ADA), ...modelsAnswer([model(), model({ id: 'mdl_a2b3c4d5e6' })]) });
    await renderApp('/dashboard');
    const link = within(sidebar()).getByRole('link', { name: /^Models/ });
    await vi.waitFor(() => expect(link.textContent).toBe('Models2'));
    expect(link.lastElementChild?.textContent).toBe('2');
  });

  it('counts the backtests beside Backtests', async () => {
    stubApi({ ...session(ADA), ...dashboardAnswer({ counts: { all: 15, starred: 5, failed: 2, public: 0 } }) });
    await renderApp('/models');
    const link = within(sidebar()).getByRole('link', { name: /^Backtests/ });
    await vi.waitFor(() => expect(link.textContent).toBe('Backtests15'));
    expect(link.lastElementChild?.textContent).toBe('15');
  });

  it('puts Experiments after Backtests, with how many backtests the beta published', async () => {
    stubApi({ ...session(ADA), ...dashboardAnswer({ experiments: { backtests: [], total: 25 } }) });
    await renderApp('/models');
    const links = within(sidebar()).getAllByRole('link');
    expect(links.map((link) => link.getAttribute('href')).slice(1, 3)).toEqual(['/backtests', '/experiments']);
    const link = within(sidebar()).getByRole('link', { name: /^Experiments/ });
    await vi.waitFor(() => expect(link.textContent).toBe('Experiments25'));
    expect(link.querySelector('svg')).not.toBeNull();
  });

  it('shows the storage the models take above the data note', async () => {
    stubApi({ ...session(ADA), ...modelsAnswer([model({ size_bytes: 25 * MB })]) });
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
    stubApi({ ...session(ADA), 'GET /api/v1/models': { status: 503, body: { detail: 'Down.' } } });
    await renderApp('/dashboard');
    expect(within(sidebar()).getByRole('link', { name: 'Models' }).textContent).toBe('Models');
    expect(within(sidebar()).queryByText('Storage')).toBeNull();
    expect(within(sidebar()).getByText('Market data delayed 15 min')).toBeDefined();
  });

  it('scrolls the open section into the middle of the row on a phone (#465)', async () => {
    const scrolled = layOutAsPhone();
    stubApi(sessionWithPreferences(ADA));
    await renderApp('/settings/api-and-mcp');
    expect(within(sidebar()).getByRole('link', { name: 'Settings' }).getAttribute('aria-current')).toBe('page');
    // Its middle (700 px) to the row's (179 px).
    expect(scrolled).toHaveBeenLastCalledWith(521);
  });

  it('scrolls nothing where the sections fit, as in the column beside the page', async () => {
    const scrolled = layOutAsPhone();
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(326);
    stubApi(sessionWithPreferences(ADA));
    await renderApp('/settings/api-and-mcp');
    expect(scrolled).not.toHaveBeenCalled();
  });
});
