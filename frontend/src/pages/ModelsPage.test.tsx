// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { modelsQueryOptions } from '@/api';
import {
  ARSEN,
  DEFAULT_PREFERENCES,
  MB,
  model,
  modelsAnswer,
  rawCandlesSummary,
  session,
  sessionWithPreferences,
  stubApi,
} from '@/test/api';
import { renderApp } from '@/test/app';

const TSMIXER = model({
  state: 'ready',
  manifest_summary: {
    representation: { id: 'renko-triples@1', params: { atr_multiple: 0.6 } },
    timeframe: '30m',
    session: 'regular',
    input_shape: [25, 23],
    warm_up: { candles: 600, kind: 'estimate' },
  },
});
const LSTM = model({
  state: 'verified',
  manifest_summary: rawCandlesSummary(),
  id: 'mdl_a2b3c4d5e6',
  name: 'lstm-signals.keras',
  size_bytes: 3 * MB,
  parameters: 9_325,
  input_shape: [25, 5],
  output_shape: null,
  starred: true,
  backtest_count: 1,
  best_sharpe: -0.184,
  created_at: '2026-10-17T08:00:00Z',
});
const DRAFT = model({
  id: 'mdl_q7r8s9t2u3',
  name: 'draft.keras',
  input_shape: null,
  backtest_count: 3,
  best_sharpe: 1.344,
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T12:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const tiles = () => screen.getAllByRole('article');
const tile = (name: string) => within(tiles().find((item) => item.textContent?.includes(name)) ?? document.body);
const subtitle = () => screen.getByRole('heading', { level: 1 }).parentElement?.querySelector('p')?.textContent;

describe('the Models page', () => {
  it('says how many models there are and the storage they take, Upload model beside it', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER, LSTM]) });
    await renderApp('/models');
    expect(await screen.findByText('2 models · 17.2 MB of 100 MB used')).toBeDefined();
    const upload = screen.getByRole('button', { name: 'Upload model' });
    expect(upload.textContent).toBe('Upload model');
    expect(upload.nextElementSibling?.textContent).toBe('Keras or ONNX, up to 50 MB');
    expect(document.title).toBe('Models · CandleStack');
  });

  it('opens the upload from an outline button, not the quick action card of the dashboard', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER]) });
    await renderApp('/models');
    const upload = await screen.findByRole('button', { name: 'Upload model' });
    // Outline: the app header's New backtest is the one primary button.
    expect(upload.className).toContain('border bg-background');
    expect(upload.className).not.toContain('bg-primary');
    // The card shows the format marks (Upload modelKerasONNX); the button does not.
    expect(screen.queryByRole('button', { name: /^Upload model\s*Keras/ })).toBeNull();
    await userEvent.setup().click(upload);
    expect(screen.getByRole('dialog', { name: 'Add a model' })).toBeDefined();
  });

  it('says how many models still need a manifest', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER, DRAFT]) });
    await renderApp('/models');
    await vi.waitFor(() => expect(subtitle()).toBe('2 models · 28.4 MB of 100 MB used · 1 needs a manifest'));
    expect(screen.getByText('1 needs a manifest').className).toContain('text-warn');
    cleanup();

    stubApi({ ...session(ARSEN), ...modelsAnswer([DRAFT, model({ id: 'mdl_x', name: 'x.keras' })]) });
    await renderApp('/models');
    await vi.waitFor(() => expect(subtitle()).toBe('2 models · 28.4 MB of 100 MB used · 2 need a manifest'));
  });

  it('says model for one, and shows no tiles without models', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([LSTM]) });
    await renderApp('/models');
    expect(await screen.findByText('1 model · 3 MB of 100 MB used')).toBeDefined();
    cleanup();

    stubApi({ ...session(ARSEN), ...modelsAnswer([]) });
    await renderApp('/models');
    expect(await screen.findByText('0 models · 0 MB of 100 MB used')).toBeDefined();
    expect(screen.queryAllByRole('article')).toEqual([]);
  });

  it('keeps the line under the title while the models load', async () => {
    stubApi({ ...session(ARSEN), 'GET /api/v1/models': () => new Promise(() => {}) });
    await renderApp('/models');
    expect(subtitle()).toBe(' ');
  });

  it('says why the models could not load', async () => {
    stubApi({ ...session(ARSEN), 'GET /api/v1/models': { status: 503, body: { detail: 'Down for a minute.' } } });
    await renderApp('/models');
    expect(await screen.findByText('Down for a minute.')).toBeDefined();
    expect(subtitle()).toBe('Down for a minute.');
  });

  it('shows a tile per model in the order of the API', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER, LSTM, DRAFT]) });
    await renderApp('/models');
    await screen.findByText(/^3 models · 31\.4 MB of 100 MB used/);
    expect(tiles().map((item) => item.querySelector('a')?.textContent)).toEqual([
      'my-tsmixer.keras',
      'lstm-signals.keras',
      'draft.keras',
    ]);
  });

  it('writes what a model is, its state, what it reads and its results', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER, LSTM, DRAFT]) });
    await renderApp('/models');
    await screen.findByText(/^3 models · 31\.4 MB of 100 MB used/);

    const tsmixer = tile('my-tsmixer.keras');
    expect(tsmixer.getByTitle('Keras')).toBeDefined();
    expect(tsmixer.getByText('Ready').getAttribute('title')).toBe('Its manifest passed every check.');
    expect(tsmixer.getByText('Keras · 14.2 MB · added 13 Oct')).toBeDefined();
    expect(tsmixer.getByText('Renko · 30m · 25 × 23')).toBeDefined();
    expect(tsmixer.getByText('–').nextElementSibling?.textContent).toBe('best Sharpe');
    expect(tsmixer.getByText('0').nextElementSibling?.textContent).toBe('backtests');
    expect(tsmixer.queryByRole('button', { name: /Add manifest/ })).toBeNull();

    const lstm = tile('lstm-signals.keras');
    expect(lstm.getByText('Verified')).toBeDefined();
    expect(lstm.getByText('Keras · 3.0 MB · added today')).toBeDefined();
    expect(lstm.getByText('Candles · 30m · 25 × 5')).toBeDefined();
    expect(lstm.getByText('−0.18')).toBeDefined();
    expect(lstm.getByText('1').nextElementSibling?.textContent).toBe('backtest');

    const draft = tile('draft.keras');
    expect(draft.getByText('Needs setup')).toBeDefined();
    expect(draft.getByText('no manifest')).toBeDefined();
    expect(draft.getByRole('button', { name: 'Add manifest to draft.keras' }).textContent).toBe('Add manifest');
    expect(draft.queryByText('best Sharpe')).toBeNull();
  });

  it('writes the shape of the file on a tile without a manifest', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([model()]) });
    await renderApp('/models');
    expect(await screen.findByText('in 25 × 23 · no manifest')).toBeDefined();
  });

  it("opens the model's page from its tile", async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER]) });
    const { router } = await renderApp('/models');
    const link = await screen.findByRole('link', { name: 'my-tsmixer.keras' });
    expect(link.getAttribute('href')).toBe('/models/mdl_7f3k2m9qx4');
    await userEvent.setup().click(link);
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/models/mdl_7f3k2m9qx4'));
  });

  it('wraps a long name to at most two lines, with the whole name in its title', async () => {
    const name = 'BLESK_ATR06_R8_OHLCV_window25_threshold005_retrained_final.keras';
    stubApi({ ...session(ARSEN), ...modelsAnswer([model({ name })]) });
    await renderApp('/models');
    const link = await screen.findByRole('link', { name });
    expect(link.getAttribute('title')).toBe(name);
    expect(link.className).toContain('line-clamp-2');
    expect(link.className).toContain('[overflow-wrap:anywhere]');
    expect(link.className).not.toContain('truncate');
  });

  it('writes the day a model was added in the date format and zone of the account', async () => {
    const appearance = { ...DEFAULT_PREFERENCES.appearance, date_format: 'iso', time_zone: 'local' } as const;
    stubApi({
      ...sessionWithPreferences(ARSEN, { ...DEFAULT_PREFERENCES, appearance }),
      ...modelsAnswer([TSMIXER, LSTM]),
    });
    await renderApp('/models');
    expect(await screen.findByText('Keras · 14.2 MB · added 2026-10-13')).toBeDefined();
    expect(tile('lstm-signals.keras').getByText('Keras · 3.0 MB · added today')).toBeDefined();
  });

  it('stars a model at once, then keeps what the API answered', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER]) });
    const { queryClient } = await renderApp('/models');
    await screen.findByText('1 model · 14.2 MB of 100 MB used');
    const star = tile('my-tsmixer.keras').getByRole('button', { name: 'Star model my-tsmixer.keras' });
    expect([star.textContent, star.title, star.getAttribute('aria-pressed')]).toEqual(['☆', 'Star', 'false']);

    const starred = model({ starred: true });
    const fetch = stubApi({
      ...modelsAnswer([starred]),
      'PATCH /api/v1/models/mdl_7f3k2m9qx4': { status: 200, body: starred },
    });
    await userEvent.setup().click(star);
    expect([star.textContent, star.title, star.getAttribute('aria-pressed')]).toEqual(['★', 'Remove star', 'true']);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/v1/models/mdl_7f3k2m9qx4', expect.anything()));
    expect(fetch.mock.calls.find(([path]) => path.endsWith('mdl_7f3k2m9qx4'))?.[1]?.body).toBe('{"starred":true}');
    await vi.waitFor(() =>
      expect(queryClient.getQueryData(modelsQueryOptions().queryKey)?.models[0]?.starred).toBe(true),
    );
  });

  it('shows the star before the API answers', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([TSMIXER]) });
    await renderApp('/models');
    await screen.findByText('1 model · 14.2 MB of 100 MB used');
    stubApi({ ...modelsAnswer([TSMIXER]), 'PATCH /api/v1/models/mdl_7f3k2m9qx4': () => new Promise(() => {}) });
    const star = tile('my-tsmixer.keras').getByRole('button', { name: 'Star model my-tsmixer.keras' });
    await userEvent.setup().click(star);
    expect(star.textContent).toBe('★');
  });

  it('puts the star back when the API refuses it', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([LSTM]) });
    await renderApp('/models');
    await screen.findByText('1 model · 3 MB of 100 MB used');
    const star = tile('lstm-signals.keras').getByRole('button', { name: 'Star model lstm-signals.keras' });
    expect(star.textContent).toBe('★');

    // Both answers wait for the test: the star must come back before the list is asked again.
    let refuse = () => undefined as void;
    let list = () => undefined as void;
    const listed = modelsAnswer([LSTM])['GET /api/v1/models'];
    stubApi({
      'GET /api/v1/models': () =>
        new Promise((resolve) => {
          list = () => listed && resolve(listed);
        }),
      'PATCH /api/v1/models/mdl_a2b3c4d5e6': () =>
        new Promise((resolve) => {
          refuse = () => resolve({ status: 503, body: { detail: 'Down.' } });
        }),
    });
    await userEvent.setup().click(star);
    expect(star.textContent).toBe('☆');

    refuse();
    await vi.waitFor(() => expect(star.textContent).toBe('★'));
    list();
    await vi.waitFor(() => expect(star.textContent).toBe('★'));
  });
});
