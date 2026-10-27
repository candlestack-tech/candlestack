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

// The manifest it ran with, in short: "Renko · 30m · 25 × 23". It is the copy stored with the run,
// checked when it was given.
export function manifestText(manifest: BacktestDocument['model']['manifest']): string {
  const { input, signature } = manifest as unknown as Partial<Manifest>;
  if (!input || !signature) return '';
  return `${manifestRepresentation(input.representation.id)} · ${input.timeframe} · ${formatGrid(signature.input.shape)}`;
}

type Decision = BacktestDocument['decision'];

// The rule that turns predictions into positions: "threshold 0.05", "hysteresis 0.3 / 0.1",
// "k-means 5 · 500 · refit 50".
function ruleText(rule: Decision['rule']): string {
  switch (rule.name) {
    case 'threshold':
      return `threshold ${rule.limit}`;
    case 'hysteresis':
      return `hysteresis ${rule.enter} / ${rule.exit}`;
    case 'kmeans':
      return `k-means ${rule.k} · ${rule.window} · refit ${rule.refit_every}`;
  }
}

// The steps before and after the rule, those that are on: "rank 500 · EMA 5 · confirm 2 · hold 3".
function stepsText(decision: Decision): string {
  const steps = [
    decision.calibrate && `${decision.calibrate.name === 'zscore' ? 'z-score' : 'rank'} ${decision.calibrate.window}`,
    decision.ema && `EMA ${decision.ema.span}`,
    decision.confirm && `confirm ${decision.confirm.candles}`,
    decision.min_hold && `hold ${decision.min_hold.candles}`,
  ];
  return steps.filter(Boolean).join(' · ');
}

const FILLS: Record<BacktestDocument['execution']['fill'], string> = {
  next_open: "next candle's open",
  signal_close: "signal candle's close",
};

// The costs and the stops, then what it is compared with: "stop loss 5 % · fees 0.1 % · slippage
// 0.02 % · vs buy and hold".
function costsText(doc: BacktestDocument): string {
  const { stop_loss, drawdown_stop, fee_bps, slippage_bps } = doc.execution;
  return [
    stop_loss != null && `stop loss ${percent(stop_loss)}`,
    drawdown_stop != null && `drawdown stop ${percent(drawdown_stop)}`,
    `fees ${bps(fee_bps)}`,
    `slippage ${bps(slippage_bps)}`,
    doc.benchmarks.length > 0 ? 'vs buy and hold' : 'no benchmark',
  ]
    .filter(Boolean)
    .join(' · ');
}

// A step of the setup: its name over what it was and the details.
export type SetupStep = { step: string; main: string; detail: string };

// The setup it ran with, in the builder's steps.
export function setupOf(doc: BacktestDocument, style: Pick<DateStyle, 'dateFormat'>): SetupStep[] {
  const { data, decision } = doc;
  return [
    { step: 'Model', main: doc.model.name, detail: manifestText(doc.model.manifest) },
    { step: 'Data', main: data.instrument.symbol, detail: `${data.timeframe} · ${periodText(data.period, style)}` },
    {
      step: 'Decision',
      main: `${ruleText(decision.rule)} · ${decision.long_only ? 'long only' : 'long and short'}`,
      detail: stepsText(decision),
    },
    { step: 'Execution', main: FILLS[doc.execution.fill], detail: costsText(doc) },
  ];
}
