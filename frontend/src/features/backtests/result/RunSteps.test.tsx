// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BacktestDocument, BacktestStep } from '@/api';
import { runningDocument } from '@/test/api';
import { step } from '@/test/steps';

import { RunSteps } from './RunSteps';

const NOW = new Date('2026-10-27T12:00:00Z');
// `seconds` before NOW, as the API writes a time.
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString();
const later = (seconds: number) => new Date(NOW.getTime() + seconds * 1000).toISOString();

// SPY over 4 Jan 2016 – 26 Oct 2026, as in the design.
function doc(steps: BacktestStep[], fields: Partial<BacktestDocument['backtest']> = {}): BacktestDocument {
  const running = runningDocument(steps);
  return {
    ...running,
    backtest: { ...running.backtest, ...fields },
    data: { ...running.data, period: { start: Date.UTC(2016, 0, 4) / 1000, end: Date.UTC(2026, 9, 27) / 1000 } },
  };
}

const CANDLES_DONE = step('candles', {
  state: 'done',
  started_at: ago(200),
  duration_s: 192.4,
  loaded: 128,
  total: 128,
  cached: 3,
  loaded_until: '2026-10-26',
  candles: 35912,
  from_cache: false,
});
const QUEUE_DONE = step('queue', { state: 'done', started_at: ago(8), duration_s: 0.04 });
const MODEL_DONE = step('model', {
  state: 'done',
  started_at: ago(8),
  duration_s: 10.3,
  load_s: 1.4,
  input_s: 2.1,
  predict_s: 6.8,
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// The rows of the steps: the name with its state for screen readers, what it says and the time, as one text each.
const rows = () => within(screen.getByRole('region', { name: 'Steps' })).getAllByRole('listitem');
const row = (name: string) => within(rows().find((item) => item.textContent?.startsWith(name))!);
const tick = (seconds: number) =>
  act(async () => {
    vi.advanceTimersByTime(seconds * 1000);
  });

describe('the steps of a running backtest', () => {
  it('shows the candles loading: how far, the months so far, the time on, the others not begun', () => {
    render(
      <RunSteps
        doc={doc([
          step('candles', {
            state: 'running',
            started_at: ago(65),
            loaded: 37,
            total: 128,
            loaded_until: '2019-01-31',
          }),
          step('queue'),
          step('model'),
          step('trades'),
        ])}
      />,
    );
    expect(rows().map((item) => item.textContent)).toEqual([
      'Load candles, runningLoaded Jan 2016 – Jan 2019The first load of a long period takes a few minutes. Later backtests take it from the cache.1 min 5 s',
      'Wait in the queue, waiting to start',
      'Run the model, waiting to start',
      'Simulate trades, waiting to start',
    ]);
    expect(screen.getByRole('progressbar', { name: 'Candles loaded' }).getAttribute('value')).toBe('29');
    expect(screen.getByText('You can close the page. The backtest keeps going.')).toBeDefined();
  });

  it('counts the time on each second and says why it takes long after 10 seconds', async () => {
    render(
      <RunSteps
        doc={doc([
          step('candles', { state: 'running', started_at: ago(5) }),
          step('queue'),
          step('model'),
          step('trades'),
        ])}
      />,
    );
    expect(row('Load candles').getByText('5 s')).toBeDefined();
    expect(screen.queryByText(/takes a few minutes/)).toBeNull();
    // Nothing loaded yet: an empty bar and no months.
    expect(screen.getByRole('progressbar').getAttribute('value')).toBe('0');
    expect(screen.queryByText(/^Loaded/)).toBeNull();
    await tick(5);
    expect(row('Load candles').getByText('10 s')).toBeDefined();
    expect(screen.getByText(/takes a few minutes/)).toBeDefined();
  });

  it('counts down the wait for the Alpaca request limit instead, until it is over', async () => {
    render(
      <RunSteps
        doc={doc([
          step('candles', {
            state: 'running',
            started_at: ago(156),
            loaded: 100,
            total: 128,
            loaded_until: '2024-04-30',
            waiting_until: later(24),
          }),
          step('queue'),
          step('model'),
          step('trades'),
        ])}
      />,
    );
    expect(screen.getByText('Loaded Jan 2016 – Apr 2024')).toBeDefined();
    expect(screen.getByText('Waiting for the Alpaca request limit. Continues in 24 s.')).toBeDefined();
    expect(screen.queryByText(/takes a few minutes/)).toBeNull();
    expect(row('Load candles').getByText('2 min 36 s')).toBeDefined();
    await tick(1);
    expect(screen.getByText('Waiting for the Alpaca request limit. Continues in 23 s.')).toBeDefined();
    // Once the wait is over it goes, though the page has not heard yet.
    await tick(30);
    expect(screen.queryByText(/Waiting for the Alpaca request limit/)).toBeNull();
    expect(screen.getByText(/takes a few minutes/)).toBeDefined();
  });

  it('shows no months while it loads the warm-up before the period', () => {
    render(
      <RunSteps
        doc={doc([
          step('candles', { state: 'running', started_at: ago(3), loaded: 1, total: 12, loaded_until: '2015-12-31' }),
          step('queue'),
          step('model'),
          step('trades'),
        ])}
      />,
    );
    expect(screen.queryByText(/^Loaded/)).toBeNull();
  });

  it('shows the candles done and the wait in the queue', () => {
    render(
      <RunSteps
        doc={doc([
          CANDLES_DONE,
          step('queue', { state: 'running', started_at: ago(2) }),
          step('model'),
          step('trades'),
        ])}
      />,
    );
    expect(rows().map((item) => item.textContent)).toEqual([
      'Load candles, done35,912 candles3 min 12 s',
      'Wait in the queue, running2 s',
      'Run the model, waiting to start',
      'Simulate trades, waiting to start',
    ]);
  });

  it('shows the model running with what it does, candles from the cache in tenths of a second', () => {
    render(
      <RunSteps
        doc={doc([
          step('candles', { state: 'done', duration_s: 0.31, candles: 412, from_cache: true }),
          QUEUE_DONE,
          step('model', { state: 'running', started_at: ago(7) }),
          step('trades'),
        ])}
      />,
    );
    expect(rows().map((item) => item.textContent)).toEqual([
      'Load candles, done412 candles · from the cache0.3 s',
      'Wait in the queue, done0 s',
      'Run the model, runningLoads the model, builds its input, predicts7 s',
      'Simulate trades, waiting to start',
    ]);
  });

  it("shows the model's parts once done while it simulates the trades", () => {
    render(
      <RunSteps
        doc={doc([CANDLES_DONE, QUEUE_DONE, MODEL_DONE, step('trades', { state: 'running', started_at: ago(1) })])}
      />,
    );
    expect(row('Run the model').getByText('load 1.4 s · input 2.1 s · predict 6.8 s')).toBeDefined();
    expect(row('Run the model').getByText('10.3 s')).toBeDefined();
    expect(row('Simulate trades').getByText('1 s')).toBeDefined();
  });

  it('shows every step as not begun when it has none yet', () => {
    render(<RunSteps doc={doc([])} />);
    expect(rows().map((item) => item.textContent)).toEqual([
      'Load candles, waiting to start',
      'Wait in the queue, waiting to start',
      'Run the model, waiting to start',
      'Simulate trades, waiting to start',
    ]);
  });
});

describe('the steps of a failed backtest', () => {
  const failed = (steps: BacktestStep[], error: string) => doc(steps, { status: 'failed', error });

  it('shows where the candles stopped, the error, that the loaded part is kept and what did not run', () => {
    const error = 'The market data source (Alpaca) is unavailable, so the candles could not be loaded.';
    render(
      <RunSteps
        doc={failed(
          [
            step('candles', {
              state: 'failed',
              started_at: ago(70),
              duration_s: 65,
              loaded: 37,
              total: 128,
              loaded_until: '2019-01-31',
            }),
            step('queue'),
            step('model'),
            step('trades'),
          ],
          error,
        )}
      />,
    );
    const candles = row('Load candles');
    expect(candles.getByText('Loaded Jan 2016 – Jan 2019')).toBeDefined();
    expect(candles.getByRole('alert').textContent).toBe(`${error}The loaded part is kept, so the next run is faster.`);
    expect(candles.getByText('1 min 5 s').className).toContain('text-down');
    expect(screen.getAllByText('not run')).toHaveLength(3);
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.queryByText('You can close the page. The backtest keeps going.')).toBeNull();
  });

  it('shows the steps before the model done and its error, without the note on kept candles', () => {
    const error = 'The 412 candles make no sample: the manifest needs at least 501. Choose a longer period.';
    render(
      <RunSteps
        doc={failed(
          [
            step('candles', { state: 'done', duration_s: 0.3, loaded: 1, total: 1, candles: 412, from_cache: true }),
            QUEUE_DONE,
            step('model', { state: 'failed', started_at: ago(2), duration_s: 1.9 }),
            step('trades'),
          ],
          error,
        )}
      />,
    );
    expect(rows().map((item) => item.textContent)).toEqual([
      'Load candles, done412 candles · from the cache0.3 s',
      'Wait in the queue, done0 s',
      `Run the model, failed${error}1.9 s`,
      'Simulate tradesnot run',
    ]);
    expect(screen.getByRole('alert').textContent).toBe(error);
  });

  it('shows the step a server restart stopped as failed', () => {
    const error = 'The server restarted while the backtest ran, so it stopped. Run it again.';
    render(
      <RunSteps
        doc={failed(
          [
            CANDLES_DONE,
            QUEUE_DONE,
            step('model', { state: 'failed', started_at: ago(4), duration_s: 4 }),
            step('trades'),
          ],
          error,
        )}
      />,
    );
    expect(row('Run the model').getByRole('alert').textContent).toBe(error);
    expect(row('Run the model').getByText('4 s')).toBeDefined();
    expect(row('Simulate trades').getByText('not run')).toBeDefined();
  });

  it('leaves the time out of a step that failed before it began', () => {
    render(
      <RunSteps
        doc={failed(
          [step('candles', { state: 'failed' }), step('queue'), step('model'), step('trades')],
          'The model was deleted.',
        )}
      />,
    );
    expect(row('Load candles').getByRole('alert').textContent).toBe('The model was deleted.');
    expect(rows()[0]?.textContent).toBe('Load candles, failedThe model was deleted.');
  });
});
