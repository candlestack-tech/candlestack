import { formatGrid, formatParams, formatWarmUp, representationName, STATE_TEXT } from '@/features/models/manifest';

import type { Model, ModelExtras } from './data';
import { button, footer, h, header, tiles, type View } from './dom';
import { formatBytes, formatCount, formatDay, formatPercent, formatSharpe, toneOf } from './format';

// The card of a model (get_model): how it sees the market, step by step from its manifest (the
// candles, the representation, the window, the network, the rule that turns its score into a
// trade), four numbers, and the total return of its best backtests as bars.

type Step = { kicker: string; title: string; detail: string; hot?: boolean };

const FRAMEWORKS: Record<string, string> = { keras: 'Keras', onnx: 'ONNX' };
const frameworkName = (framework: string) => FRAMEWORKS[framework] ?? framework;

const SESSIONS: Record<string, string> = { regular: 'regular session', '24/7': 'around the clock' };

// The rule of the model's best backtest: a score past the threshold opens a position.
function decisionStep(extras: ModelExtras | null): Step {
  const decision = extras?.decision;
  if (!decision) return { kicker: 'Decision', title: 'Score → trade', detail: 'set by each backtest' };
  const side = decision.direction === 'long' ? ' · long only' : '';
  return { kicker: 'Decision', title: 'Score → trade', detail: `|score| > ${decision.threshold} opens${side}` };
}

// A representation's parameters in short: Renko bricks sized by the ATR as "brick 0.6 × ATR(14)".
function paramsOf(params: Record<string, unknown>): string {
  const { atr_multiple: multiple, atr_window: window } = params;
  if (typeof multiple === 'number' && typeof window === 'number') return `brick ${multiple} × ATR(${window})`;
  const written = formatParams(params);
  return written === 'none' ? 'as the candles come' : written;
}

function steps(model: Model, extras: ModelExtras | null): Step[] {
  const summary = model.manifest_summary;
  if (!summary) return [];
  const [rows, features] = summary.input_shape;
  return [
    {
      kicker: 'Data',
      title: 'Candles',
      detail: `${summary.timeframe} · ${SESSIONS[summary.session] ?? summary.session}`,
    },
    {
      kicker: 'Representation',
      title: representationName(summary.representation.id, true),
      detail: paramsOf(summary.representation.params),
    },
    { kicker: 'Input', title: 'Window', detail: `${rows ?? '?'} rows × ${features ?? '?'} features` },
    {
      kicker: 'Model',
      title: `${frameworkName(model.framework)} network`,
      detail: `${formatCount(model.parameters)} weights`,
      hot: true,
    },
    decisionStep(extras),
  ];
}

function pipeline(model: Model, extras: ModelExtras | null): HTMLElement[] {
  const shown = steps(model, extras);
  if (shown.length === 0) {
    return [
      h('p', { class: 'note' }, 'No manifest yet: add one in CandleStack to say how the model reads the market.'),
    ];
  }
  return [
    h('div', { class: 'caption' }, 'How the model sees the market'),
    h(
      'ol',
      { class: 'steps' },
      ...shown.map(({ kicker, title, detail, hot }) =>
        h(
          'li',
          { class: hot ? 'step hot' : 'step' },
          h('span', { class: 'kicker' }, kicker),
          h('span', { class: 'step-title' }, title),
          h('span', { class: 'step-detail' }, detail),
        ),
      ),
    ),
  ];
}

function numbers(model: Model): HTMLElement {
  const summary = model.manifest_summary;
  const output = model.output_shape ? formatGrid(model.output_shape) : '?';
  const input = summary?.input_shape ?? model.input_shape;
  return tiles([
    { label: 'Backtests', value: formatCount(model.backtest_count) },
    { label: 'Best Sharpe', value: formatSharpe(model.best_sharpe ?? null), tone: toneOf(model.best_sharpe) },
    { label: 'Warm-up', value: summary ? formatWarmUp(summary) : '–' },
    { label: 'Input → output', value: input ? `${input.join('×')} → ${output.replaceAll(' ', '')}` : '–' },
  ]);
}

function bars(extras: ModelExtras | null): HTMLElement[] {
  const backtests = extras?.backtests.filter((backtest) => backtest.total_return !== null) ?? [];
  if (backtests.length === 0) return [];
  const widest = Math.max(...backtests.map((backtest) => Math.abs(backtest.total_return ?? 0)), 1e-9);
  return [
    h('div', { class: 'caption rule' }, 'Backtests with this model, total return'),
    h(
      'div',
      { class: 'bars' },
      ...backtests.map(({ name, symbols, total_return: total }) => {
        const tone = toneOf(total) ?? '';
        const width = `${((Math.abs(total ?? 0) / widest) * 100).toFixed(1)}%`;
        return h(
          'div',
          { class: 'bar-row' },
          h('span', { class: 'bar-name', title: name }, [name, ...symbols].join(' · ')),
          h('span', { class: 'bar-track' }, h('span', { class: `bar ${tone}`, style: `width: ${width}` })),
          h('span', { class: `num ${tone}` }, formatPercent(total)),
        );
      }),
    ),
  ];
}

export function modelCard(model: Model, extras: ModelExtras | null, view: View): HTMLElement {
  const url = model.url;
  const best = extras?.backtests[0];
  const subtitle = [
    frameworkName(model.framework),
    `${formatCount(model.parameters)} weights`,
    formatBytes(model.size_bytes),
    `uploaded ${formatDay(Date.parse(model.created_at) / 1000)}`,
  ].join(' · ');
  return h(
    'section',
    { class: 'card', 'aria-label': `Model ${model.name}` },
    header(model.name, subtitle, {
      text: STATE_TEXT[model.state].toLowerCase(),
      tone: model.state === 'needs_setup' ? 'warn' : undefined,
    }),
    ...pipeline(model, extras),
    numbers(model),
    ...bars(extras),
    footer(
      url && button('Open model in CandleStack', url, view.open, true),
      best && button('Best backtest', best.url, view.open),
    ),
  );
}
