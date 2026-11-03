import type { Backtest, BacktestDocument, Experiment, Manifest, Representation } from '@/api';
import { formatGrid, manifestRepresentation } from '@/features/models';

import { benchmarkName } from '../result/format';
import { instrumentList, symbolsOf } from '../format';

// How Experiments writes a published backtest (public-experiments: gCard, eTable, reads).

// What a model reads, as its manifest names it (Settings, Models): the windows of candles are
// Candles, as `candles@1` is.
const READS: Record<Representation, string> = { renko: 'Renko', kagi: 'Kagi', windows: 'Candles' };

export const representationLabel = (representation: Representation) => READS[representation];

// "Renko · 30m · 25 × 23"; without the model's input shape "Renko · 30m".
export function readsText(experiment: Pick<Experiment, 'representation' | 'model' | 'setup'>): string {
  const { input_shape: shape } = experiment.model;
  const parts = [representationLabel(experiment.representation), experiment.setup.timeframe];
  return (shape ? [...parts, formatGrid(shape)] : parts).join(' · ');
}

// What the benchmark is called: buy and hold of the backtest's own instrument, else its ETF.
export const benchmarkLabel = (benchmark: Experiment['setup']['benchmark']) =>
  benchmark === 'bh' ? 'Buy and hold' : benchmark;

// The author's first name, for "More by Tymur" and "The model file stays with Tymur".
export const firstName = (name: string) => name.trim().split(/\s+/)[0] ?? name;

// What the comparison needs of a published backtest: another account's carries its author's
// name, for the initials in the panel and the notice when it is unpublished.
export const experimentTarget = (experiment: Experiment) => ({
  id: experiment.id,
  number: experiment.number,
  status: 'finished' as const,
  symbol: instrumentList(symbolsOf(experiment)),
  author: experiment.author.mine ? undefined : experiment.author.name,
});

// What a card shows, from a published backtest or, for the preview of Publish, from the document
// of one about to be.
export type CardFacts = {
  id: string;
  number: number;
  instruments: Backtest['instruments'];
  model: { name: string; framework: string };
  representation: string;
  timeframe: string;
  input: string | null;
  strategy: readonly number[] | null;
  benchmark: { label: string; series: readonly number[] | null; value: number | null };
  totalReturn: number | null;
  sharpe: number | null;
  maxDrawdown: number | null;
  trades: number | null;
  author: { name: string; mine: boolean };
  // Null in the preview: not published yet.
  publishedAt: string | null;
};

export function cardOf(experiment: Experiment): CardFacts {
  const { model, setup, equity } = experiment;
  return {
    id: experiment.id,
    number: experiment.number,
    instruments: experiment.instruments,
    model: { name: model.name, framework: model.framework },
    representation: representationLabel(experiment.representation),
    timeframe: setup.timeframe,
    input: model.input_shape ? formatGrid(model.input_shape) : null,
    strategy: equity?.strategy ?? null,
    benchmark: {
      label: benchmarkLabel(setup.benchmark),
      series: equity?.benchmark ?? null,
      value: experiment.benchmark_return,
    },
    totalReturn: experiment.total_return,
    sharpe: experiment.sharpe,
    maxDrawdown: experiment.max_drawdown,
    trades: experiment.num_trades,
    author: experiment.author,
    publishedAt: experiment.published_at,
  };
}

// The card's line has about as many points as the API's short series.
const POINTS = 120;

// Every n-th value of a long series, its last one kept, as returns from 0.
function sampled(values: readonly (number | null)[]): number[] {
  const known = values.filter((value): value is number => value !== null);
  const step = Math.max(1, Math.ceil(known.length / POINTS));
  const picked = known.filter((_, index) => index % step === 0 || index === known.length - 1);
  return picked.map((value) => value - 1);
}

type Results = NonNullable<BacktestDocument['results']>;

// The card a finished backtest gets once published, as its author sees it in the dialog.
export function cardOfDocument(doc: BacktestDocument, results: Results): CardFacts {
  const { input, signature } = doc.model.manifest as unknown as Partial<Manifest>;
  const benchmark = doc.benchmarks[0];
  const { stats, series } = results;
  return {
    id: doc.backtest.id,
    number: doc.backtest.number,
    instruments: [
      { symbol: doc.data.instrument.symbol, market: doc.data.instrument.market === 'crypto' ? 'crypto' : 'stock' },
    ],
    model: { name: doc.model.name, framework: doc.model.framework },
    representation: input ? manifestRepresentation(input.representation.id) : '',
    timeframe: doc.data.timeframe,
    input: signature ? formatGrid(signature.input.shape) : null,
    strategy: sampled(series.columns.equity),
    benchmark: {
      label: benchmark ? benchmarkName(doc, benchmark) : 'Buy and hold',
      series: benchmark ? sampled(series.benchmarks[benchmark.id] ?? []) : null,
      value: benchmark ? (results.benchmarks[benchmark.id]?.total_return ?? null) : stats.buy_and_hold_return,
    },
    totalReturn: stats.total_return,
    sharpe: stats.sharpe,
    maxDrawdown: stats.max_drawdown,
    trades: stats.num_trades,
    author: { name: doc.backtest.author.name, mine: true },
    publishedAt: null,
  };
}
