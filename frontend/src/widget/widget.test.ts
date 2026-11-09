// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  BACKTEST_CURVES,
  backtestAnswer,
  backtestResult,
  comparisonAnswer,
  comparisonResult,
  CURVES,
  EQUITY,
  MODEL_EXTRAS,
  modelAnswer,
  modelResult,
} from '@/test/widget';

import { connect, PROTOCOL_VERSION, type Host, type ToolResult } from './bridge';
import { cardOf, CHART_KEY, type Card } from './data';
import { filled, opens, render } from './view';

// The widget draws a tool's result as a card: what each card shows of an answer, how it asks the
// server for what a result came without, and how it speaks MCP Apps with the chat client.

const opened: string[] = [];
const view = { width: 600, open: (url: string) => opened.push(url) };

function drawn(result: ToolResult): HTMLElement {
  const card = cardOf(result);
  if (!card) throw new Error('No card for the result');
  const element = render(card, view);
  document.body.replaceChildren(element);
  return element;
}

const texts = (element: Element, selector: string) =>
  [...element.querySelectorAll(selector)].map((node) => node.textContent);

afterEach(() => {
  opened.length = 0;
  document.body.replaceChildren();
});

describe('the backtest card', () => {
  it('shows the equity against buy and hold, the statistics and the link to its page', () => {
    const card = drawn(backtestResult());
    expect(card.getAttribute('aria-label')).toBe('Backtest Silent Orion');
    expect(card.querySelector('.sub')?.textContent).toBe(
      'SPY · 30m · 3 Jun 2024 – 12 Jul 2024 · long and short · raw-candles.keras',
    );
    expect(texts(card, '.legend > span')).toEqual(['Strategy −5.6 %', 'Buy and hold SPY +13.4 %']);
    expect(card.querySelectorAll('path.line')).toHaveLength(2);
    expect(card.querySelector('[aria-label="Drawdown, deepest −14.6 %"] path.fall')).not.toBeNull();
    expect(texts(card, '.tile-value')).toEqual([
      '−5.6 %',
      '−14.6 %',
      '−0.44',
      '−0.60',
      '52.7 %',
      '349',
      '78.9 %',
      '+13.4 %',
    ]);
    expect(card.querySelector('.tile-value')?.className).toContain('down');
    expect(card.querySelector('.aside')?.textContent).toBe('3,250 candles · ran in 7 s');
  });

  it('has the host open its links', () => {
    const card = drawn(backtestResult());
    const [open, compare] = card.querySelectorAll('a');
    open?.click();
    compare?.click();
    expect(opened).toEqual([
      'https://app.candlestack.tech/backtests/bt_7f3k2m9qx4',
      'https://app.candlestack.tech/backtests/compare?ids=bt_7f3k2m9qx4',
    ]);
  });

  it('shows where a backtest without results stands', () => {
    const card = drawn({ structuredContent: backtestAnswer('running') });
    expect(card.querySelector('.pill')?.textContent).toBe('running');
    expect(card.querySelector('.note')?.textContent).toContain('is running');
    expect(card.querySelector('svg.chart')).toBeNull();
    expect(card.querySelector('.tiles')).toBeNull();
  });

  it('draws the curve a model asked for when the result has no chart data', () => {
    const card = cardOf({ structuredContent: { ...backtestAnswer(), equity: EQUITY } });
    expect(card?.kind === 'backtest' && card.chart).toMatchObject({
      times: EQUITY.ts,
      equity: EQUITY.equity,
      benchmark: { label: 'Buy and hold SPY', total: 0.1339 },
    });
  });

  it('draws the stored curves from 1 at the start', () => {
    const card = cardOf(backtestResult());
    const chart = card?.kind === 'backtest' ? card.chart : null;
    expect(chart?.equity[0]).toBe(1);
    expect(chart?.benchmark?.values.at(-1)).toBeCloseTo(1 + 0.0034 * 39);
  });
});

describe('the comparison card', () => {
  it('shows each backtest on one time axis and the statistics with the best marked', () => {
    const card = drawn(comparisonResult());
    expect(card.querySelector('.title')?.textContent).toBe('2 backtests compared');
    expect(card.querySelector('.sub')?.textContent).toBe('Same model · differ in instrument, fee and period');
    expect(texts(card, '.legend > span')).toEqual(['Silent Orion · SPY', 'Quiet Vega · MU']);
    expect(card.querySelectorAll('path.line')).toHaveLength(2);
    // A backtest outside its period has a gap: its line starts at its first value.
    expect(card.querySelector('path.slot-2')?.getAttribute('d')?.match(/M/g)).toHaveLength(1);
    expect(texts(card, 'thead th')).toEqual(['Metric', 'Silent Orion', 'Quiet Vega']);
    const firstRow = card.querySelector('tbody tr');
    expect(texts(firstRow!, 'td')).toEqual(['best−5.6 %', '−95.1 %']);
    expect(texts(card, 'tbody th')).toContain('Buy and hold');
  });

  it('links the Compare page of the answer', () => {
    const card = drawn(comparisonResult());
    card.querySelector('a')?.click();
    expect(opened).toEqual(['https://app.candlestack.tech/backtests/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5']);
    expect(card.querySelector('.aside')?.textContent).toBe('12 metrics in the app');
  });

  it('draws the first four of more and links their Compare page', () => {
    const answer = comparisonAnswer();
    const backtests = [0, 1, 2, 3, 4].map((index) => ({
      ...answer.backtests[0],
      id: `bt_${index}`,
      name: `Run ${index}`,
    }));
    const card = drawn({ structuredContent: { ...answer, backtests, url: null } });
    expect(texts(card, 'thead th')).toHaveLength(5);
    const link = card.querySelector('a');
    expect(link?.textContent).toBe('Compare these 4 in CandleStack');
    link?.click();
    expect(opened).toEqual(['https://app.candlestack.tech/backtests/compare?ids=bt_0,bt_1,bt_2,bt_3']);
    expect(card.querySelector('.aside')?.textContent).toBe('First 4 of 5 shown');
  });

  it('links the page of a single backtest', () => {
    const answer = comparisonAnswer();
    const card = drawn({ structuredContent: { ...answer, backtests: answer.backtests.slice(0, 1), url: null } });
    expect(card.querySelector('.title')?.textContent).toBe('1 backtest');
    expect(card.querySelector('.sub')?.textContent).toBe('SPY');
    card.querySelector('a')?.click();
    expect(opened).toEqual(['https://app.candlestack.tech/backtests/bt_7f3k2m9qx4']);
  });
});

describe('a link', () => {
  it('opens only pages of the app the card comes from', () => {
    const card = cardOf(backtestResult()) as Card;
    expect(opens(card, 'https://app.candlestack.tech/backtests/compare?ids=bt_1')).toBe(true);
    expect(opens(card, 'https://evil.test/backtests/bt_1')).toBe(false);
    expect(opens(card, 'http://app.candlestack.tech/backtests/bt_1')).toBe(false);
    expect(opens(card, 'javascript:alert(1)')).toBe(false);
  });
});

describe('the model card', () => {
  it('shows how the model sees the market, its numbers and its backtests', () => {
    const card = drawn(modelResult());
    expect(card.querySelector('.sub')?.textContent).toBe('Keras · 410,717 weights · 4.98 MB · uploaded 27 Oct 2026');
    expect(card.querySelector('.pill')?.textContent).toBe('ready');
    expect(texts(card, '.step-title')).toEqual(['Candles', 'Renko bricks', 'Window', 'Keras network', 'Score → trade']);
    expect(texts(card, '.step-detail')).toEqual([
      '30m · regular session',
      'brick 0.6 × ATR(14)',
      '25 rows × 23 features',
      '410,717 weights',
      '|score| > 0.05 opens',
    ]);
    expect(texts(card, '.tile-value')).toEqual(['2', '−0.44', '≈ 403 candles', '25×23 → 1']);
    expect(texts(card, '.bar-name')).toEqual(['Silent Orion · SPY', 'Quiet Vega · MU']);
    const widths = [...card.querySelectorAll<HTMLElement>('.bar')].map((bar) => bar.style.width);
    expect(widths).toEqual(['5.9%', '100%']);
  });

  it('opens the model and its best backtest', () => {
    const card = drawn(modelResult());
    card.querySelectorAll('a').forEach((link) => link.click());
    expect(opened).toEqual([modelAnswer().url, MODEL_EXTRAS.backtests[0]?.url]);
  });

  it('says a model without a manifest has no pipeline yet', () => {
    const card = drawn({ structuredContent: { ...modelAnswer(), manifest_summary: null, state: 'needs_setup' } });
    expect(card.querySelector('.note')?.textContent).toContain('No manifest yet');
    expect(card.querySelector('.pill')?.textContent).toBe('needs setup');
  });
});

describe('a result the widget does not draw', () => {
  it('is an error or another answer', () => {
    expect(cardOf({ isError: true, structuredContent: backtestAnswer() })).toBeNull();
    expect(cardOf({ structuredContent: { id: 'x' } })).toBeNull();
    expect(cardOf({})).toBeNull();
  });
});

describe('what a result came without', () => {
  const host = (answer: Record<string, unknown>) => {
    const callTool = vi.fn(async () => ({ structuredContent: answer }));
    const fake: Host = { onResult: vi.fn(), onTheme: vi.fn(), openLink: vi.fn(), callTool, resized: vi.fn() };
    return { fake, callTool };
  };

  it('is the stored curves of the backtests drawn, asked of the server through the host', async () => {
    localStorage.clear();
    const backtest = host({ ...comparisonAnswer(), ...BACKTEST_CURVES });
    const card = (await filled(cardOf(backtestResult(false)) as Card, backtestResult(false), backtest.fake)) as Card;
    expect(backtest.callTool).toHaveBeenCalledWith('compare_backtests', { ids: ['bt_7f3k2m9qx4'], curve_points: 200 });
    expect(card.kind === 'backtest' && card.chart?.times).toEqual(BACKTEST_CURVES.times);

    const comparison = host({ ...comparisonResult(false).structuredContent, ...CURVES });
    const compared = await filled(cardOf(comparisonResult(false)) as Card, comparisonResult(false), comparison.fake);
    expect(comparison.callTool).toHaveBeenCalledWith('compare_backtests', {
      ids: ['bt_7f3k2m9qx4', 'bt_t9n8v7d6a5'],
      curve_points: 200,
    });
    expect(compared?.kind === 'compare' && compared.curves?.times).toEqual(CURVES.times);
  });

  it('is asked once: the curves of finished backtests are kept', async () => {
    localStorage.clear();
    const backtest = host({ ...comparisonAnswer(), ...BACKTEST_CURVES });
    const result = backtestResult(false);
    await filled(cardOf(result) as Card, result, backtest.fake);
    const again = await filled(cardOf(result) as Card, result, backtest.fake);
    expect(backtest.callTool).toHaveBeenCalledTimes(1);
    expect(again?.kind === 'backtest' && again.chart?.times).toEqual(BACKTEST_CURVES.times);
  });

  it('for a model is its best backtests', async () => {
    const row = {
      id: 'bt_7f3k2m9qx4',
      name: 'Silent Orion',
      instruments: [{ market: 'stock', symbol: 'SPY' }],
      total_return: -0.0563,
      sharpe: -0.44,
      url: 'https://app.candlestack.tech/backtests/bt_7f3k2m9qx4',
      setup: { threshold: 0.05, direction: 'both' },
    };
    const model = host({ backtests: [row] });
    const card = await filled(cardOf(modelResult(false)) as Card, modelResult(false), model.fake);
    expect(model.callTool).toHaveBeenCalledWith(
      'list_backtests',
      expect.objectContaining({ model_id: 'mdl_k2m9qx47f3', sort: 'sharpe' }),
    );
    expect(card?.kind === 'model' && card.extras).toEqual({
      backtests: [{ ...MODEL_EXTRAS.backtests[0] }],
      decision: { threshold: 0.05, direction: 'both' },
    });
  });

  it('is not asked when the result has it', async () => {
    const { fake, callTool } = host({});
    for (const result of [backtestResult(), comparisonResult(), modelResult()]) {
      expect(await filled(cardOf(result) as Card, result, fake)).toBeNull();
    }
    expect(callTool).not.toHaveBeenCalled();
  });
});

describe('the bridge to the chat client', () => {
  // The chat client: a frame's window whose messages the widget gets as the parent's.
  function chatClient() {
    const frame = document.createElement('iframe');
    document.body.append(frame);
    const parent = frame.contentWindow as Window;
    const sent: { id?: number; method?: string; params?: Record<string, unknown> }[] = [];
    const reply = (data: unknown) => window.dispatchEvent(new MessageEvent('message', { data, source: parent }));
    vi.spyOn(parent, 'postMessage').mockImplementation((message: (typeof sent)[number]) => {
      sent.push(message);
      if (message.method === 'ui/initialize') {
        reply({
          jsonrpc: '2.0',
          id: message.id,
          result: { protocolVersion: PROTOCOL_VERSION, hostContext: { theme: 'light' } },
        });
      }
    });
    return { parent, sent, reply };
  }

  it('initializes with MCP Apps, gets the result and the theme, and opens links through the host', async () => {
    const client = chatClient();
    const connected = await connect(client.parent);
    expect(connected?.theme).toBe('light');
    expect(client.sent.map(({ method }) => method)).toEqual(['ui/initialize', 'ui/notifications/initialized']);
    expect(client.sent[0]?.params).toMatchObject({
      protocolVersion: PROTOCOL_VERSION,
      appInfo: { name: 'CandleStack' },
    });

    const results: ToolResult[] = [];
    const themes: string[] = [];
    connected?.host.onResult((result) => results.push(result));
    connected?.host.onTheme((theme) => themes.push(theme));
    const result = backtestResult();
    client.reply({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: result });
    client.reply({ jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { theme: 'dark' } });
    expect(results[0]?._meta?.[CHART_KEY]).toEqual(BACKTEST_CURVES);
    expect(themes).toEqual(['dark']);

    connected?.host.openLink('https://app.candlestack.tech/backtests/bt_7f3k2m9qx4');
    connected?.host.resized(480);
    expect(client.sent.slice(2).map(({ method, params }) => [method, params])).toEqual([
      ['ui/open-link', { url: 'https://app.candlestack.tech/backtests/bt_7f3k2m9qx4' }],
      ['ui/notifications/size-changed', { height: 480 }],
    ]);
  });

  it('answers a request of the host and ignores messages of other windows', async () => {
    const client = chatClient();
    const connected = await connect(client.parent);
    const results: ToolResult[] = [];
    connected?.host.onResult((result) => results.push(result));
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: {} },
        source: window,
      }),
    );
    client.reply({ jsonrpc: '2.0', id: 7, method: 'ui/resource-teardown', params: { reason: 'closed' } });
    expect(results).toEqual([]);
    expect(client.sent.at(-1)).toEqual({ jsonrpc: '2.0', id: 7, result: {} });
  });

  it('is none outside a frame', async () => {
    expect(await connect(window)).toBeNull();
  });
});
