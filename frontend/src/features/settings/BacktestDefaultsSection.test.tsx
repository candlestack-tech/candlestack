// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { preferencesQueryOptions } from '@/api';
import { ARSEN, DEFAULT_PREFERENCES, problem, sentBody, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp, warmUpSettings } from '@/test/app';

beforeAll(warmUpSettings);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SAVE = 'PUT /api/v1/me/preferences/backtest-defaults';

const card = () => within(screen.getByRole('region', { name: 'Backtest defaults' }));
const save = () => card().getByRole('button', { name: 'Save defaults' });
const field = (name: string) => screen.getByLabelText<HTMLInputElement>(name);

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
    expect(save()).toHaveProperty('disabled', false);
  });

  it('draws no line between the title and the first row', async () => {
    stubApi(sessionWithPreferences(ARSEN));
    await renderApp('/settings/backtest-defaults');
    // The rows' top line is left out right after the card's title (rows.tsx), so the first row has
    // to follow it in the card.
    const first = card().getByText('Timeframe').closest('[data-slot=settings-row]');
    expect(first?.previousElementSibling?.getAttribute('data-slot')).toBe('card-head');
  });

  it('saves the choices: the timeframe, the direction, the threshold and the stop loss', async () => {
    const changed = {
      ...DEFAULT_PREFERENCES.backtest_defaults,
      timeframe: '1h' as const,
      direction: 'both' as const,
      threshold: 0.07,
      stop_loss_pct: 6,
    };
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: { status: 200, body: changed } });
    const { queryClient } = await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    const defaults = card();

    await user.click(defaults.getByRole('radio', { name: '1h' }));
    await user.click(defaults.getByRole('radio', { name: /^Long and short/ }));
    screen.getByLabelText('Threshold').focus();
    await user.keyboard('{ArrowRight}{ArrowRight}');
    screen.getByLabelText('Stop loss').focus();
    await user.keyboard('{ArrowLeft}');
    expect(defaults.getByText('0.07')).toBeDefined();
    expect(defaults.getByText('6 %')).toBeDefined();

    await user.click(save());
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toEqual(changed));
    await vi.waitFor(() =>
      expect(queryClient.getQueryData(preferencesQueryOptions().queryKey)?.backtest_defaults).toEqual(changed),
    );
    expect(save()).toHaveProperty('disabled', false);
  });

  it('saves the typed fields as numbers and days, a comma working as the decimal point', async () => {
    const changed = { ...DEFAULT_PREFERENCES.backtest_defaults, period_start: '2022-01-03', fees_pct: 0.25 };
    const fetch = stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: { status: 200, body: changed } });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();

    await retype(user, 'From', '2022-01-03');
    await retype(user, 'Fees', '0,25');
    await user.click(save());
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toEqual(changed));
  });

  it('picks the benchmark from its list, each with what it is', async () => {
    const fetch = stubApi({
      ...sessionWithPreferences(ARSEN),
      [SAVE]: { status: 200, body: { ...DEFAULT_PREFERENCES.backtest_defaults, benchmark: 'QQQ' } },
    });
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
    await user.click(save());
    await vi.waitFor(() => expect(sentBody(fetch, SAVE)).toMatchObject({ benchmark: 'QQQ' }));
  });

  it('marks a typed field that is not right and sends nothing while one is, or nothing changed', async () => {
    const fetch = stubApi(sessionWithPreferences(ARSEN));
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await user.click(save());
    await retype(user, 'To', '2020-12-31');
    expect(field('To').getAttribute('aria-invalid')).toBe('true');
    await user.click(save());

    await retype(user, 'To', '2025-06-30');
    await retype(user, 'Slippage', '9');
    expect(field('To').getAttribute('aria-invalid')).toBeNull();
    expect(field('Slippage').getAttribute('aria-invalid')).toBe('true');
    await user.click(save());
    expect(sentBody(fetch, SAVE)).toBeUndefined();
  });

  it('says why the defaults could not be saved', async () => {
    stubApi({ ...sessionWithPreferences(ARSEN), [SAVE]: { status: 422, body: { detail: 'Threshold too high.' } } });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await user.click(card().getByRole('radio', { name: '1d' }));
    await user.click(save());
    expect((await card().findByRole('alert')).textContent).toBe('Threshold too high.');
  });

  it("says in plain words when the API refuses a value, not in the API's", async () => {
    stubApi({
      ...sessionWithPreferences(ARSEN),
      [SAVE]: problem(422, 'validation', "body 'threshold': Input should be less than or equal to 0.5"),
    });
    await renderApp('/settings/backtest-defaults');
    const user = userEvent.setup();
    await user.click(card().getByRole('radio', { name: '1d' }));
    await user.click(save());
    expect((await card().findByRole('alert')).textContent).toBe(
      'Could not save the defaults. Check the values and try again.',
    );
  });
});
