// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { preferencesQueryOptions, type BacktestDefaults } from '@/api';
import { ARSEN, DEFAULT_PREFERENCES, problem, sentBody, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SAVE = 'PUT /api/v1/me/preferences/backtest-defaults';
const SAVED = DEFAULT_PREFERENCES.backtest_defaults;

// The API's answer to a save: what it was sent.
const echo = async (init: RequestInit | undefined) => ({ status: 200, body: JSON.parse(init?.body as string) });

const card = () => within(screen.getByRole('region', { name: 'Backtest defaults' }));
const field = (name: string) => screen.getByLabelText<HTMLInputElement>(name);
const indicator = () => card().getByRole('status');

// Every body the stub got for the saves, in order.
const saves = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls
    .filter(([path, init]) => path === '/api/v1/me/preferences/backtest-defaults' && init?.method === 'PUT')
    .map(([, init]) => JSON.parse(init?.body as string) as BacktestDefaults);

// Replaces what a field holds, as a paste.
async function retype(user: ReturnType<typeof userEvent.setup>, name: string, text: string) {
  await user.clear(field(name));
  await user.paste(text);
}

describe('Backtest defaults', () => {
  it('shows the saved defaults in every control of the design', async () => {
    stubApi(sessionWithPreferences(ARSEN));
    await renderApp('/settings/backtest-defaults');
    expect(card().getByText('New backtests start here')).toBeDefined();
    const timeframes = within(card().getByRole('radiogroup', { name: 'Timeframe' })).getAllByRole('radio');
    expect(timeframes.map((radio) => [radio.textContent, radio.getAttribute('aria-checked')])).toEqual([
      ['1m', 'false'],
      ['5m', 'false'],
      ['15m', 'false'],
      ['30m', 'true'],
      ['1h', 'false'],
      ['4h', 'false'],
      ['1d', 'false'],
    ]);
    expect([field('From').value, field('To').value]).toEqual(['2021-01-04', '2024-12-31']);
    const directions = within(card().getByRole('radiogroup', { name: 'Direction' })).getAllByRole('radio');
    expect(directions.map((radio) => radio.textContent)).toEqual([
      'Long onlyBuy on a signal, sell on the exit',
      'Long and shortAlso sell short when it points down',
    ]);
    expect(directions[0]?.getAttribute('aria-checked')).toBe('true');
    expect(card().getByRole('slider', { name: 'Threshold' }).getAttribute('aria-valuetext')).toBe('0.05');
    expect(card().getByRole('slider', { name: 'Stop loss' }).getAttribute('aria-valuetext')).toBe('7 %');
    expect(card().getByText('0.05')).toBeDefined();
    expect([field('Fees').value, field('Slippage').value]).toEqual(['0.1', '0.02']);
    expect(card().getByText('% per side')).toBeDefined();
    expect(card().getByRole('combobox', { name: 'Benchmark: Buy and hold' }).textContent).toBe('B&HBuy and hold');
    expect(card().queryByRole('button', { name: /save/i })).toBeNull();
  });

  it('draws no line between the title and the first row', async () => {
    stubApi(sessionWithPreferences(ARSEN));
    await renderApp('/settings/backtest-defaults');
    // The rows' top line is left out right after the card's title (rows.tsx), so the first row has
    // to follow it in the card.
    const first = card().getByText('Timeframe').closest('[data-slot=settings-row]');
    expect(first?.previousElementSibling?.getAttribute('data-slot')).toBe('card-head');
  });

  it('saves the whole set on each click: the timeframe, then the direction', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    const { queryClient } = await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();

    await user.click(card().getByRole('radio', { name: '1h' }));
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toEqual({ ...SAVED, timeframe: '1h' }));
    await vi.waitFor(() => expect(indicator().textContent).toBe('Saved'));
    await user.click(card().getByRole('radio', { name: /^Long and short/ }));
    const changed = { ...SAVED, timeframe: '1h', direction: 'both' };
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toEqual(changed));
    await vi.waitFor(() =>
      expect(queryClient.getQueryData(preferencesQueryOptions().queryKey)?.backtest_defaults).toEqual(changed),
    );
  });

  it('saves a slider once it is set: let go, or moved with a key', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();

    screen.getByLabelText('Threshold').focus();
    await user.keyboard('{ArrowRight}{ArrowRight}');
    screen.getByLabelText('Stop loss').focus();
    await user.keyboard('{ArrowLeft}');
    expect(card().getByText('0.07')).toBeDefined();
    expect(card().getByText('6 %')).toBeDefined();
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toEqual({ ...SAVED, threshold: 0.07, stop_loss_pct: 6 }));
  });

  it('saves a typed field on leaving it, as numbers and days, a comma working as the decimal point', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();

    await retype(user, 'From', '2022-01-03');
    expect(sentBody(fetch, SAVE)).toBeUndefined();
    await user.tab();
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toEqual({ ...SAVED, period_start: '2022-01-03' }));

    await retype(user, 'Fees', '0,25');
    await user.keyboard('{Enter}');
    await vi.waitFor(() =>
      expect(sentBody(fetch, SAVE)).toEqual({ ...SAVED, period_start: '2022-01-03', fees_pct: 0.25 }),
    );
    await user.tab();
    expect(saves(fetch)).toHaveLength(2);
  });

  it('puts the saved value back on Esc and sends nothing', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await retype(user, 'Slippage', '9');
    await user.keyboard('{Escape}');
    expect(field('Slippage').value).toBe('0.02');
    await user.tab();
    expect(sentBody(fetch, SAVE)).toBeUndefined();
    expect(indicator().textContent).toBe('');
  });

  it('picks the benchmark from its list, each with what it is, and saves it', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await user.click(card().getByRole('combobox', { name: 'Benchmark: Buy and hold' }));

    const options = screen.getAllByRole('option');
    expect(options.map((option) => option.textContent)).toEqual([
      'B&HBuy and holdThe same instruments, bought and kept',
      'SPYSPDR S&P 500 ETFThe 500 largest US companies',
      'QQQInvesco QQQThe Nasdaq 100',
      'DIASPDR Dow Jones ETFThe 30 Dow Jones companies',
    ]);
    expect(options[0]?.getAttribute('aria-selected')).toBe('true');
    await user.click(screen.getByRole('option', { name: 'Invesco QQQ' }));

    expect(card().getByRole('combobox', { name: 'Benchmark: Invesco QQQ' }).textContent).toBe('QQQInvesco QQQ');
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toMatchObject({ benchmark: 'QQQ' }));
  });

  it('sends nothing while a typed field is not right: the field says what to fix, the card Not saved', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await retype(user, 'To', '2020-12-31');
    await user.tab();
    expect(field('To').getAttribute('aria-invalid')).toBe('true');
    expect(card().getByText('The test period must end after it starts. Not saved until fixed.')).toBeDefined();
    expect(indicator().textContent).toBe('Not saved');

    await user.click(card().getByRole('radio', { name: '1d' }));
    expect(indicator().textContent).toBe('Not saved');
    expect(sentBody(fetch, SAVE)).toBeUndefined();

    await retype(user, 'To', '2025-06-30');
    await user.tab();
    await vi.waitFor(() =>
      expect(sentBody(fetch, SAVE)).toEqual({ ...SAVED, period_end: '2025-06-30', timeframe: '1d' }),
    );
    expect(field('To').getAttribute('aria-invalid')).toBeNull();
    expect(card().queryByText(/Not saved until fixed/)).toBeNull();

    await retype(user, 'Slippage', '9');
    await user.keyboard('{Enter}');
    expect(field('Slippage').getAttribute('aria-invalid')).toBe('true');
    expect(card().getByText('Slippage is 0 to 5 %. Not saved until fixed.')).toBeDefined();
    expect(indicator().textContent).toBe('Not saved');
    expect(saves(fetch)).toHaveLength(1);
  });

  it("says Couldn't save and sends the latest set again with Retry", async () => {
    const answers = [{ status: 503, body: { detail: 'Down for a minute.' } }];
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      [SAVE]: async (init) => answers.shift() ?? echo(init),
    });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await user.click(card().getByRole('radio', { name: '1d' }));

    expect((await card().findByRole('alert')).textContent).toBe('Down for a minute.');
    expect(indicator().textContent).toBe("Couldn't save");
    expect(card().getByRole('radio', { name: '1d' }).getAttribute('aria-checked')).toBe('true');
    await user.click(card().getByRole('button', { name: 'Retry' }));
    await vi.waitFor(() => expect(indicator().textContent).toBe('Saved'));
    expect(saves(fetch)).toEqual([
      { ...SAVED, timeframe: '1d' },
      { ...SAVED, timeframe: '1d' },
    ]);
  });

  it("says in plain words when the API refuses a value, not in the API's", async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      [SAVE]: problem(422, 'validation', "body 'threshold': Input should be less than or equal to 0.5"),
    });
    await renderApp('/settings/backtest-defaults');
    await userEvent.setup().click(card().getByRole('radio', { name: '1d' }));
    expect((await card().findByRole('alert')).textContent).toBe(
      'Could not save the defaults. Check the values and try again.',
    );
  });

  it('lets the last change win: one save at a time, the ones in between skipped, a slow answer no harm', async () => {
    let answerFirst = () => undefined as void;
    const first = new Promise<void>((resolve) => (answerFirst = resolve));
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      [SAVE]: async (init) => {
        if (saves(fetch).length === 1) await first;
        return echo(init);
      },
    });
    const { queryClient } = await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();

    await user.click(card().getByRole('radio', { name: '1h' }));
    await user.click(card().getByRole('radio', { name: '4h' }));
    await user.click(card().getByRole('radio', { name: '1d' }));
    expect(saves(fetch)).toEqual([{ ...SAVED, timeframe: '1h' }]);
    answerFirst();

    await vi.waitFor(() => expect(indicator().textContent).toBe('Saved'));
    expect(saves(fetch).map((sent) => sent.timeframe)).toEqual(['1h', '1d']);
    expect(queryClient.getQueryData(preferencesQueryOptions().queryKey)?.backtest_defaults.timeframe).toBe('1d');
  });

  it('asks before leaving with a field to fix: Keep editing stays, Discard leaves', async () => {
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    const { router } = await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await retype(user, 'Fees', '7,5');
    await user.click(screen.getByRole('link', { name: 'Profile' }));

    const question = await screen.findByRole('dialog', { name: 'Discard the change?' });
    expect(screen.getByText('Fees are 0 to 5 %. Not saved until fixed.')).toBeDefined();
    await user.click(within(question).getByRole('button', { name: 'Keep editing' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(router.state.location.pathname).toBe('/settings/backtest-defaults');
    expect(field('Fees').value).toBe('7,5');

    await user.click(screen.getByRole('link', { name: 'Profile' }));
    await user.click(await screen.findByRole('button', { name: 'Discard' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/settings/profile'));
    expect(sentBody(fetch, SAVE)).toBeUndefined();
  });

  it('leaves without asking once every field is right', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: echo });
    const { router } = await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await retype(user, 'Fees', '0.2');
    await user.click(screen.getByRole('link', { name: 'Profile' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/settings/profile'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
