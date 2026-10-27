import type { BacktestDocument, BacktestStep, Manifest } from '@/api';
import { formatGrid, manifestRepresentation } from '@/features/models';
import { formatDate, formatMonth, type DateStyle } from '@/lib/dates';
import { formatCount, formatPercent, formatSharpe, formatWinRate, MISSING, returnTone } from '@/lib/results';

// How a backtest's page writes its document (mockups/app: runHead, runStats, resA, runSetupOf).
// Results are fractions, market time is UTC epoch seconds of a candle's open.

type Stats = NonNullable<BacktestDocument['results']>['stats'];
type Tone = 'up' | 'down' | undefined;

// A metric as the page shows it: its name, the value written out and its colour, if any.
export type Metric = { name: string; value: string; tone: Tone };

// The classes of a metric's colour.
export const TONE = { up: 'text-up', down: 'text-down' } as const;

const toneOf = (fraction: number | null): Tone => (fraction === null ? undefined : returnTone(fraction));
// A fall is red unless there was none.
const fallTone = (fraction: number | null): Tone => (fraction === null || fraction === 0 ? undefined : 'down');

const metric = (name: string, value: string, tone?: Tone): Metric => ({ name, value, tone });

// The strip under the header: the numbers people look at first.
export function basicMetrics(stats: Stats): Metric[] {
  return [
    metric('Return', formatPercent(stats.total_return), toneOf(stats.total_return)),
    metric('Buy and hold', formatPercent(stats.buy_and_hold_return)),
    metric('Sharpe', formatSharpe(stats.sharpe)),
    metric('Max drawdown', formatPercent(stats.max_drawdown), fallTone(stats.max_drawdown)),
    metric('Trades', formatCount(stats.num_trades)),
    metric('Win rate', formatWinRate(stats.win_rate)),
  ];
}

// Every statistic of the engine (docs/engine.md, Metrics), in its order. Fees are the sum of each
// trade's fees as a share of its capital, so they read as a percent like the time in the market.
export function allMetrics(stats: Stats): Metric[] {
  return [
    metric('Total return', formatPercent(stats.total_return), toneOf(stats.total_return)),
    metric('Max drawdown', formatPercent(stats.max_drawdown), fallTone(stats.max_drawdown)),
    metric('Sharpe', formatSharpe(stats.sharpe)),
    metric('Sortino', formatSharpe(stats.sortino)),
    metric('Trades', formatCount(stats.num_trades)),
    metric('Win rate', formatWinRate(stats.win_rate)),
    metric('Avg. trade', formatPercent(stats.avg_trade_return)),
    metric('Best trade', formatPercent(stats.best_trade_return), toneOf(stats.best_trade_return)),
    metric('Worst trade', formatPercent(stats.worst_trade_return), toneOf(stats.worst_trade_return)),
    metric('Time in market', formatWinRate(stats.exposure)),
    metric('Fees paid', formatWinRate(stats.fees_paid)),
    metric('Buy and hold', formatPercent(stats.buy_and_hold_return), toneOf(stats.buy_and_hold_return)),
  ];
}

// The days of the test period, `[start, end)` in UTC: "4 Jan 2021 – 31 Dec 2024".
export function periodText({ start, end }: { start: number; end: number }, style: Pick<DateStyle, 'dateFormat'>) {
  const utc = { timeZone: 'UTC', dateFormat: style.dateFormat };
  return `${formatDate(new Date(start * 1000), utc)} – ${formatDate(new Date((end - 1) * 1000), utc)}`;
}

// How long it ran: "42 s", "2 min 42 s".
export function durationText(seconds: number): string {
  const whole = Math.round(seconds);
  return whole < 60 ? `${whole} s` : `${Math.floor(whole / 60)} min ${whole % 60} s`;
}

// The steps of a run as its page names them, in their order.
export const STEP_NAMES: Record<BacktestStep['id'], string> = {
  candles: 'Load candles',
  queue: 'Wait in the queue',
  model: 'Run the model',
  trades: 'Simulate trades',
};

// How long a step took: tenths under a minute ("0 s", "0.3 s", "10.3 s"), then as durationText.
export function stepTime(seconds: number | null): string {
  if (seconds === null) return MISSING;
  return seconds < 59.95 ? `${Number(seconds.toFixed(1))} s` : durationText(seconds);
}

const DAY_MS = 86_400_000;

// How far the candles' load got, from the period's first month to the month of the last day
// loaded: "Loaded Jan 2016 – Jan 2019". Nothing while it loads the warm-up before the period.
export function loadedText(loadedUntil: string | null, periodStart: number): string {
  if (loadedUntil === null) return '';
  const until = new Date(`${loadedUntil}T00:00:00Z`);
  const start = new Date(periodStart * 1000);
  if (until.getTime() + DAY_MS <= start.getTime()) return '';
  const utc = { timeZone: 'UTC' };
  return `Loaded ${formatMonth(start, utc)} – ${formatMonth(until, utc)}`;
}

// What the done candles' step loaded: "35,912 candles", "412 candles · from the cache".
export function candlesText({ candles, from_cache }: BacktestStep): string {
  if (candles === null) return '';
  return `${formatCount(candles)} candles${from_cache ? ' · from the cache' : ''}`;
}

// The worker's times of the done model's step: "load 1.4 s · input 2.1 s · predict 6.8 s".
export function modelText({ load_s, input_s, predict_s }: BacktestStep): string {
  const parts = [
    ['load', load_s],
    ['input', input_s],
    ['predict', predict_s],
  ] as const;
  return parts
    .filter(([, seconds]) => seconds !== null)
    .map(([name, seconds]) => `${name} ${stepTime(seconds)}`)
    .join(' · ');
}

// A fraction as a plain percent without trailing zeros: 0.05 → "5 %", 0.001 → "0.1 %".
const percent = (fraction: number) => `${Number((fraction * 100).toFixed(4))} %`;
// Basis points as a percent: 10 → "0.1 %".
const bps = (value: number) => percent(value / 10_000);

// One line of the setup: what it is and its value.
export type SetupRow = readonly [label: string, value: string];

// The model's rows: what it reads, its candles and its input ("Candles", "30m", "25 × 23"), from
// the manifest stored with the run, checked when it was given. The candles are the run's when the
// manifest does not say.
function modelRows(doc: BacktestDocument): SetupRow[] {
  const { input, signature } = doc.model.manifest as unknown as Partial<Manifest>;
  const rows: (SetupRow | undefined)[] = [
    input && ['Reads', manifestRepresentation(input.representation.id)],
    ['Timeframe', input?.timeframe ?? doc.data.timeframe],
    signature && ['Input', formatGrid(signature.input.shape)],
  ];
  return rows.filter((row) => row !== undefined);
}

type Decision = BacktestDocument['decision'];

const candles = (count: number) => `${formatCount(count)} ${count === 1 ? 'candle' : 'candles'}`;

// The rule that turns predictions into positions, with its settings in the builder's words.
function ruleRows(rule: Decision['rule']): SetupRow[] {
  switch (rule.name) {
    case 'threshold':
      return [['Threshold', String(rule.limit)]];
    case 'hysteresis':
      return [
        ['Enter at', String(rule.enter)],
        ['Leave at', String(rule.exit)],
      ];
    case 'kmeans':
      return [
        ['K-means groups', String(rule.k)],
        ['K-means window', formatCount(rule.window)],
        ['Refit every', candles(rule.refit_every)],
      ];
  }
}

// The steps before the rule and those after it, the ones that are on.
function stepRows(decision: Decision): { before: SetupRow[]; after: SetupRow[] } {
  const { calibrate, ema, confirm, min_hold } = decision;
  const on = (rows: (SetupRow | null | undefined)[]) => rows.filter((row) => row != null);
  return {
    before: on([
      calibrate && [
        'Calibration',
        `${calibrate.name === 'zscore' ? 'Z-score' : 'Rank'} ${formatCount(calibrate.window)}`,
      ],
      ema && ['Smoothing', `EMA ${ema.span}`],
    ]),
    after: on([
      confirm && ['Confirm', candles(confirm.candles)],
      min_hold && ['Minimum hold', candles(min_hold.candles)],
    ]),
  };
}

const FILLS: Record<BacktestDocument['execution']['fill'], string> = {
  next_open: "Next candle's open",
  signal_close: "Signal candle's close",
};

const BENCHMARKS: Record<string, string> = { buy_and_hold: 'Buy and hold' };

// The fill, the stops and the costs, then what it is compared with.
function executionRows(doc: BacktestDocument): SetupRow[] {
  const { fill, stop_loss, drawdown_stop, fee_bps, slippage_bps } = doc.execution;
  const rows: (SetupRow | false)[] = [
    ['Fills at', FILLS[fill]],
    stop_loss != null && ['Stop loss', percent(stop_loss)],
    drawdown_stop != null && ['Drawdown stop', percent(drawdown_stop)],
    ['Fees', bps(fee_bps)],
    ['Slippage', bps(slippage_bps)],
    ...doc.benchmarks.map((benchmark): SetupRow => ['Benchmark', BENCHMARKS[benchmark.kind] ?? benchmark.kind]),
  ];
  return rows.filter((row) => row !== false);
}

// A group of the setup (Model, Data, Decision, Execution) with one value per row. The box puts
// the model's and the instrument's names above their rows.
export type SetupGroup = { name: 'Model' | 'Data' | 'Decision' | 'Execution'; rows: SetupRow[] };

// The setup it ran with, in the builder's steps. The period's days are UTC, as in the header.
export function setupOf(doc: BacktestDocument, style: Pick<DateStyle, 'dateFormat'>): SetupGroup[] {
  const { data, decision } = doc;
  const day = (seconds: number) =>
    formatDate(new Date(seconds * 1000), { timeZone: 'UTC', dateFormat: style.dateFormat });
  const steps = stepRows(decision);
  return [
    { name: 'Model', rows: modelRows(doc) },
    {
      name: 'Data',
      rows: [
        ['From', day(data.period.start)],
        ['To', day(data.period.end - 1)],
        ...(data.rows ? [['Candles', formatCount(data.rows.count)] as const] : []),
      ],
    },
    {
      name: 'Decision',
      rows: [
        ...steps.before,
        ...ruleRows(decision.rule),
        ...steps.after,
        ['Positions', decision.long_only ? 'Long only' : 'Long and short'],
      ],
    },
    { name: 'Execution', rows: executionRows(doc) },
  ];
}
