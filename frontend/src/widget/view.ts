import { backtestCard } from './backtest';
import type { Host, ToolResult } from './bridge';
import { compareCard, SHOWN_MAX } from './compare';
import { cardOf, CHART_KEY, missing, type Card, type Comparison, type Curves, type ModelExtras } from './data';
import type { View } from './dom';
import { modelCard } from './model';

// The longest curve the server draws (backend CURVE_POINTS) and the most backtests the model
// card lists.
const POINTS = 200;
const MODEL_BACKTESTS = 6;

export function render(card: Card, view: View): HTMLElement {
  switch (card.kind) {
    case 'backtest':
      return backtestCard(card.answer, card.chart, view);
    case 'compare':
      return compareCard(card.answer, card.curves, view);
    case 'model':
      return modelCard(card.answer, card.extras, view);
  }
}

// A page of the app the card's links lead to: the card's own links name the app's origin.
function appOrigin(card: Card): string | null {
  const url =
    card.kind === 'backtest'
      ? card.answer.backtest.url
      : card.kind === 'compare'
        ? card.answer.backtests[0]?.url
        : (card.answer.url ?? card.extras?.backtests[0]?.url);
  try {
    return url ? new URL(url).origin : null;
  } catch {
    return null;
  }
}

// Whether a link may be opened: a page of the app, over HTTPS (or on a developer's own machine).
export function opens(card: Card, url: string): boolean {
  try {
    const target = new URL(url);
    const secure = target.protocol === 'https:' || target.hostname === 'localhost';
    return secure && target.origin === appOrigin(card);
  } catch {
    return false;
  }
}

type Row = {
  id: string;
  name: string;
  instruments: { symbol: string }[];
  total_return: number | null;
  sharpe: number | null;
  url: string;
  setup: { threshold: number; direction: string };
};

// The model card's backtests from list_backtests, as the server would have put them in _meta.
function modelExtras(answer: Record<string, unknown> | undefined): ModelExtras {
  const rows = (answer?.backtests ?? []) as Row[];
  const best = rows[0];
  return {
    backtests: rows.map((row) => ({
      id: row.id,
      name: row.name,
      symbols: row.instruments.map(({ symbol }) => symbol),
      total_return: row.total_return,
      sharpe: row.sharpe,
      url: row.url,
    })),
    decision: best ? { threshold: best.setup.threshold, direction: best.setup.direction } : null,
  };
}

// The curves of finished backtests never change: a card asked for them once keeps them in the
// frame's storage, so a conversation opened again does not ask again. Storage may be refused
// in a sandboxed frame; the card then just asks.
const CACHE = 'candlestack.widget.v1:';
const CACHE_DAYS = 7;

function cached(key: string): unknown {
  try {
    const kept = JSON.parse(localStorage.getItem(CACHE + key) ?? 'null') as { at: number; chart: unknown } | null;
    return kept && Date.now() - kept.at < CACHE_DAYS * 86_400_000 ? kept.chart : null;
  } catch {
    return null;
  }
}

function keep(key: string, chart: unknown) {
  try {
    localStorage.setItem(CACHE + key, JSON.stringify({ at: Date.now(), chart }));
  } catch {
    // Full or refused: the next card asks again.
  }
}

const curvesOf = (answer: Record<string, unknown> | undefined): Curves | null => {
  const comparison = answer as Comparison | undefined;
  return comparison?.times?.length ? { times: comparison.times, backtests: comparison.backtests } : null;
};

// What the card asks for: the stored curves of the backtests it draws (compare_backtests, which
// reads only what each run stored), or the model's best backtests (list_backtests).
async function ask(card: Card, host: Host): Promise<unknown> {
  switch (card.kind) {
    case 'backtest': {
      const ids = [card.answer.backtest.id];
      return curvesOf((await host.callTool('compare_backtests', { ids, curve_points: POINTS })).structuredContent);
    }
    case 'compare': {
      const ids = card.answer.backtests.slice(0, SHOWN_MAX).map(({ id }) => id);
      return curvesOf((await host.callTool('compare_backtests', { ids, curve_points: POINTS })).structuredContent);
    }
    case 'model': {
      const more = await host.callTool('list_backtests', {
        model_id: card.answer.id,
        status: ['finished'],
        sort: 'sharpe',
        order: 'desc',
        per_page: MODEL_BACKTESTS,
        include_facets: false,
      });
      return modelExtras(more.structuredContent);
    }
  }
}

// Only what will not change is kept: finished backtests' curves, not a model's list.
function cacheKey(card: Card): string | null {
  if (card.kind === 'backtest') return `backtest:${card.answer.backtest.id}`;
  if (card.kind === 'compare' && card.answer.backtests.every(({ status }) => status === 'finished')) {
    return `compare:${card.answer.backtests
      .slice(0, SHOWN_MAX)
      .map(({ id }) => id)
      .join(',')}`;
  }
  return null;
}

// The card with what it draws beyond the answer, asked of the server through the host when the
// result came without it (a client that shows the widget without saying so); null when it needs
// nothing or got nothing.
export async function filled(card: Card, result: ToolResult, host: Host): Promise<Card | null> {
  if (!missing(card)) return null;
  const key = cacheKey(card);
  let chart = key ? cached(key) : null;
  if (!chart) {
    chart = await ask(card, host);
    if (key && chart) keep(key, chart);
  }
  return chart ? cardOf({ ...result, _meta: { ...result._meta, [CHART_KEY]: chart } }) : null;
}
