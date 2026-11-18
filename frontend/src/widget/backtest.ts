import { drawdownChart, lineChart } from './chart';
import type { Backtest, BacktestChart } from './data';
import { button, footer, h, header, tiles, type View } from './dom';
import {
  fallTone,
  formatCount,
  formatPercent,
  formatPeriod,
  formatSeconds,
  formatSharpe,
  formatWinRate,
  symbolOf,
  toneOf,
} from './format';

// The card of one backtest (get_backtest): the equity against its benchmark, the drawdown under
// it, eight statistics and the link to its page. A backtest that has not finished shows where it
// stands instead of results.

const STATUS = { queued: 'queued', running: 'running', finished: 'finished', failed: 'failed' } as const;

function subtitle({ setup }: Backtest): string {
  const { data, decision, model } = setup;
  return [
    symbolOf(data.instrument.id),
    data.timeframe,
    formatPeriod(data.period.start, data.period.end),
    decision.long_only ? 'long only' : 'long and short',
    model.name,
  ].join(' · ');
}

function legend(items: { className: string; text: string }[]): HTMLElement {
  return h(
    'div',
    { class: 'legend' },
    ...items.map(({ className, text }) =>
      h('span', {}, h('span', { class: `key ${className}`, 'aria-hidden': 'true' }), text),
    ),
  );
}

function charts(answer: Backtest, chart: BacktestChart, width: number): Element[] {
  const lines = [{ values: chart.equity, className: 'strategy' }];
  const items = [{ className: 'strategy', text: `Strategy ${formatPercent(answer.metrics?.total_return ?? null)}` }];
  if (chart.benchmark) {
    lines.unshift({ values: chart.benchmark.values, className: 'benchmark' });
    items.push({ className: 'benchmark', text: `${chart.benchmark.label} ${formatPercent(chart.benchmark.total)}` });
  }
  const deepest = formatPercent(answer.metrics?.max_drawdown ?? null);
  return [
    legend(items),
    lineChart(
      {
        times: chart.times,
        lines,
        base: 1,
        baseLabel: 'start 1.00',
        label: 'Equity curve of the strategy against buy and hold',
      },
      [width, 190],
    ),
    h('div', { class: 'caption' }, 'Drawdown'),
    drawdownChart(chart.times, chart.drawdown, `Drawdown, deepest ${deepest}`, width),
  ];
}

function statistics(answer: Backtest): HTMLElement | null {
  const stats = answer.metrics;
  if (!stats) return null;
  return tiles([
    { label: 'Total return', value: formatPercent(stats.total_return), tone: toneOf(stats.total_return) },
    { label: 'Max drawdown', value: formatPercent(stats.max_drawdown), tone: fallTone(stats.max_drawdown) },
    { label: 'Sharpe', value: formatSharpe(stats.sharpe), tone: toneOf(stats.sharpe) },
    { label: 'Sortino', value: formatSharpe(stats.sortino), tone: toneOf(stats.sortino) },
    { label: 'Win rate', value: formatWinRate(stats.win_rate) },
    { label: 'Trades', value: formatCount(stats.num_trades) },
    { label: 'Time in market', value: formatWinRate(stats.exposure) },
    { label: 'Buy and hold', value: formatPercent(stats.buy_and_hold_return), tone: toneOf(stats.buy_and_hold_return) },
  ]);
}

// Where a backtest without results stands.
function standing({ backtest }: Backtest): HTMLElement {
  const text =
    backtest.status === 'failed'
      ? (backtest.error ?? 'The backtest failed.')
      : `The backtest is ${backtest.status}: its results appear on its page once it finished.`;
  return h('p', { class: 'note' }, text);
}

function facts(answer: Backtest): string {
  const parts = [];
  if (answer.candles) parts.push(`${formatCount(answer.candles)} candles`);
  if (answer.backtest.duration_s) parts.push(`ran in ${formatSeconds(answer.backtest.duration_s)}`);
  return parts.join(' · ');
}

// The comparison page with this backtest, where the user adds others.
const compareUrl = (url: string, id: string) => `${url.slice(0, url.lastIndexOf('/'))}/compare?ids=${id}`;

export function backtestCard(answer: Backtest, chart: BacktestChart | null, view: View): HTMLElement {
  const { backtest } = answer;
  const status = STATUS[backtest.status as keyof typeof STATUS] ?? backtest.status;
  const tone = status === 'failed' ? 'down' : status === 'finished' ? undefined : 'warn';
  return h(
    'section',
    { class: 'card', 'aria-label': `Backtest ${backtest.name}` },
    header(backtest.name, subtitle(answer), { text: status, tone }),
    ...(chart ? charts(answer, chart, view.width) : []),
    status === 'finished' ? statistics(answer) : standing(answer),
    footer(
      button('Open in CandleStack', backtest.url, view.open, true),
      button('Compare with other runs', compareUrl(backtest.url, backtest.id), view.open),
      h('span', { class: 'aside' }, facts(answer)),
    ),
  );
}
