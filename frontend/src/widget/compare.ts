import { lineChart } from './chart';
import type { Compared, Comparison, Curves } from './data';
import { button, footer, h, header, type Tone, type View } from './dom';
import { fallTone, formatCount, formatPercent, formatSharpe, formatWinRate, symbolOf, toneOf } from './format';

// The card of a comparison (compare_backtests): each backtest's equity on one time axis, the main
// statistics side by side with the best marked as the app marks it, and the link to the Compare
// page. It shows the first four, in the app's slot colours; the page has every one.

export const SHOWN_MAX = 4;

type Row = {
  key: string;
  label: string;
  write: (value: number | null) => string;
  tone: (value: number | null) => Tone;
};

const ROWS: Row[] = [
  { key: 'total_return', label: 'Total return', write: formatPercent, tone: toneOf },
  { key: 'max_drawdown', label: 'Max drawdown', write: formatPercent, tone: fallTone },
  { key: 'sharpe', label: 'Sharpe', write: formatSharpe, tone: toneOf },
  { key: 'win_rate', label: 'Win rate', write: formatWinRate, tone: () => undefined },
  { key: 'num_trades', label: 'Trades', write: formatCount, tone: () => undefined },
  { key: 'buy_and_hold_return', label: 'Buy and hold', write: formatPercent, tone: toneOf },
];

// A setup field of `differs` in words: "period.start" is the period, "execution.fee_bps" the fee.
function fieldName(path: string): string {
  const parts = path.split('.');
  const field = ['decision', 'execution'].includes(parts[0] ?? '') ? (parts.at(-1) ?? path) : (parts[0] ?? path);
  return field.replace(/_(bps|pct)$/, '').replaceAll('_', ' ');
}

const instrumentOf = (backtest: Compared, shared: Record<string, unknown> | null | undefined) => {
  const id = backtest.setup.instrument ?? shared?.instrument;
  return typeof id === 'string' ? symbolOf(id) : '';
};

function subtitle({ differs, backtests, shared_setup: shared }: Comparison): string {
  if (backtests.length === 1 && backtests[0]) {
    const [only] = backtests;
    const timeframe = typeof only.setup.timeframe === 'string' ? only.setup.timeframe : '';
    return [instrumentOf(only, shared), timeframe].filter(Boolean).join(' · ');
  }
  const fields = [...new Set(differs.map(fieldName))];
  const same = differs.some((path) => path.startsWith('model')) ? [] : ['Same model'];
  if (fields.length === 0) return [...same, 'same setup'].join(' · ');
  const last = fields.pop();
  const listed = fields.length ? `${fields.join(', ')} and ${last}` : last;
  return [...same, `differ in ${listed}`].join(' · ');
}

function chart(shown: Compared[], curves: Curves, width: number): SVGElement | null {
  const byId = new Map(curves.backtests.map((curve) => [curve.id, curve.equity]));
  const lines = shown.flatMap((backtest, slot) => {
    const values = byId.get(backtest.id);
    return values ? [{ values, className: `slot-${slot + 1}` }] : [];
  });
  if (lines.length === 0 || curves.times.length < 2) return null;
  return lineChart(
    { times: curves.times, lines, base: 0, baseLabel: '0 %', label: `Equity curves of ${shown.length} backtests` },
    [width, 200],
  );
}

function cell(row: Row, comparison: Comparison, index: number): HTMLElement {
  const metric = comparison.metrics.find(({ key }) => key === row.key);
  const value = metric?.values[index] ?? null;
  const best = metric?.marks[index] === 'best';
  return h(
    'td',
    { class: `num ${row.tone(value) ?? ''}` },
    best && h('span', { class: 'best' }, 'best'),
    row.write(value),
  );
}

function table(shown: Compared[], comparison: Comparison): HTMLElement {
  const head = h(
    'tr',
    {},
    h('th', { scope: 'col' }, 'Metric'),
    ...shown.map((backtest, slot) => h('th', { scope: 'col', class: `num slot-${slot + 1}` }, backtest.name)),
  );
  const rows = ROWS.map((row) =>
    h('tr', {}, h('th', { scope: 'row' }, row.label), ...shown.map((_backtest, index) => cell(row, comparison, index))),
  );
  return h('div', { class: 'table' }, h('table', {}, h('thead', {}, head), h('tbody', {}, ...rows)));
}

// The link of the footer: the answer's Compare page, which the server gives for 2 to 4; one
// backtest's own page; the Compare page of the four drawn when there are more (the page shows
// four at most). An older server's answer has no `url`: the page is built beside a backtest's.
function link(comparison: Comparison, shown: Compared[]): { url: string; text: string } | null {
  const [first] = comparison.backtests;
  if (!first?.url) return null;
  if (comparison.backtests.length === 1) return { url: first.url, text: 'Open in CandleStack' };
  const drawn = `${first.url.slice(0, first.url.lastIndexOf('/'))}/compare?ids=${shown.map(({ id }) => id).join(',')}`;
  if (shown.length < comparison.backtests.length) {
    return { url: drawn, text: `Compare these ${shown.length} in CandleStack` };
  }
  return { url: comparison.url ?? drawn, text: 'Open comparison in CandleStack' };
}

export function compareCard(comparison: Comparison, curves: Curves | null, view: View): HTMLElement {
  const shown = comparison.backtests.slice(0, SHOWN_MAX);
  const more = comparison.backtests.length - shown.length;
  const count = comparison.backtests.length;
  const opens = link(comparison, shown);
  return h(
    'section',
    { class: 'card', 'aria-label': `Comparison of ${count} backtests` },
    header(count === 1 ? '1 backtest' : `${count} backtests compared`, subtitle(comparison)),
    h(
      'div',
      { class: 'legend' },
      ...shown.map((backtest, slot) =>
        h(
          'span',
          {},
          h('span', { class: `key slot-${slot + 1}`, 'aria-hidden': 'true' }),
          [backtest.name, instrumentOf(backtest, comparison.shared_setup)].filter(Boolean).join(' · '),
        ),
      ),
    ),
    curves && chart(shown, curves, view.width),
    table(shown, comparison),
    footer(
      opens && button(opens.text, opens.url, view.open, true),
      h('span', { class: 'aside' }, more > 0 ? `First ${shown.length} of ${count} shown` : '12 metrics in the app'),
    ),
  );
}
