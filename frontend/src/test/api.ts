import { vi } from 'vitest';

import type {
  Backtest,
  BacktestDocument,
  BacktestsPage,
  Catalog,
  CatalogBlock,
  Dashboard,
  Manifest,
  Me,
  Model,
  Preferences,
} from '@/api';

// A signed-in user as GET /api/v1/me sends it.
export const ARSEN: Me = {
  id: 'usr_7f3k2m9qx4',
  display_name: 'Arsen Labovich',
  email: 'arsen@example.com',
  providers: ['github'],
  sign_in_methods: [{ provider: 'github', email: 'arsen@example.com', login: 'arsen' }],
  created_at: '2026-10-17T08:00:00Z',
};

type Answer = { status: number; body?: unknown };

// The API's answer when this browser has no session.
export const NOT_SIGNED_IN: Answer = {
  status: 401,
  body: {
    type: 'https://candlestack.tech/problems/not-signed-in',
    title: 'Not signed in',
    status: 401,
    detail: 'Sign in to use this.',
  },
};

// An answer the test gives when it wants: a request still running, or one that is aborted.
type Later = (init: RequestInit | undefined) => Promise<Answer>;

// Stubs fetch with the given answers, by method and path ("GET /api/v1/me", as src/api/client.ts
// calls it); anything else is a 404. Returns the stub, to check what was asked.
export function stubApi(answers: Record<string, Answer | Later>) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const given = answers[`${init?.method ?? 'GET'} ${path}`] ?? {
      status: 404,
      body: { title: 'Not found' },
    };
    const answer = typeof given === 'function' ? await given(init) : given;
    const body = answer.body === undefined ? null : JSON.stringify(answer.body);
    return new Response(body, { status: answer.status });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

// The answers of a session: GET /api/v1/me gives the user, or 401 without one.
export const session = (user: Me | null): Record<string, Answer> => ({
  'GET /api/v1/me': user === null ? NOT_SIGNED_IN : { status: 200, body: user },
});

// GET /api/v1/me/preferences of a user who never changed a setting: the API's defaults.
export const DEFAULT_PREFERENCES: Preferences = {
  backtest_defaults: {
    timeframe: '30m',
    period_start: '2021-01-04',
    period_end: '2024-12-31',
    direction: 'long',
    threshold: 0.05,
    stop_loss_pct: 7,
    fees_pct: 0.1,
    slippage_pct: 0.02,
    benchmark: 'bh',
  },
  appearance: { theme: 'dark', time_zone: 'ny', date_format: 'dmy', time_format: '24', reduce_motion: false },
};

// The answers of a session with its preferences.
export const sessionWithPreferences = (user: Me, preferences: Preferences = DEFAULT_PREFERENCES) => ({
  ...session(user),
  'GET /api/v1/me/preferences': { status: 200, body: preferences },
});

// The JSON body of the request the stub got for `method path`, the last one if there were several.
export function sentBody(fetch: ReturnType<typeof stubApi>, request: string): unknown {
  const [method, path] = request.split(' ');
  const call = fetch.mock.calls.findLast(([url, init]) => url === path && (init?.method ?? 'GET') === method);
  const body = call?.[1]?.body;
  return typeof body === 'string' ? JSON.parse(body) : undefined;
}

// A problem+json answer of the API, with its extensions (`check`, `used_bytes`, ...).
export const problem = (status: number, slug: string, detail: string, extensions: object = {}): Answer => ({
  status,
  body: { type: `https://candlestack.tech/problems/${slug}`, title: slug, status, detail, ...extensions },
});

export const MB = 1024 * 1024;

// An uploaded model as the API sends it: 25 candles by 23 features in, one number out.
export const model = (fields: Partial<Model> = {}): Model => ({
  id: 'mdl_7f3k2m9qx4',
  name: 'my-tsmixer.keras',
  framework: 'keras',
  size_bytes: Math.round(14.2 * MB),
  parameters: 1_240_000,
  input_shape: [25, 23],
  output_shape: [1],
  starred: false,
  backtest_count: 0,
  best_sharpe: null,
  created_at: '2026-10-13T09:00:00Z',
  state: 'needs_setup',
  manifest_summary: null,
  ...fields,
});

// A finished backtest as the API sends it: #12 on AAPL and MSFT with blesk-novol-8.keras.
export const backtest = (fields: Partial<Backtest> = {}): Backtest => ({
  id: 'bt_7f3k2m9qx4',
  number: 12,
  instruments: [
    { symbol: 'AAPL', market: 'stock' },
    { symbol: 'MSFT', market: 'stock' },
  ],
  representation: 'windows',
  model: { id: 'mdl_7f3k2m9qx4', name: 'blesk-novol-8.keras', framework: 'keras' },
  setup: {
    timeframe: '30m',
    period_start: '2021-01-04',
    period_end: '2024-12-31',
    direction: 'long',
    threshold: 0.05,
    stop_loss_pct: 7,
    fees_pct: 0.1,
    slippage_pct: 0.02,
    benchmark: 'bh',
  },
  status: 'finished',
  starred: false,
  total_return: 0.246,
  buy_and_hold_return: 0.682,
  sharpe: 0.79,
  max_drawdown: -0.197,
  win_rate: 0.512,
  num_trades: 1712,
  duration_s: 162,
  equity: { strategy: [0, 0.031, 0.118, 0.246], benchmark: [0, 0.12, 0.35, 0.682] },
  error: null,
  created_at: '2026-10-17T14:05:00Z',
  finished_at: '2026-10-17T14:07:42Z',
  ...fields,
});

// The same backtest as it failed: no results, an error.
export const failedBacktest = (fields: Partial<Backtest> = {}): Backtest =>
  backtest({
    status: 'failed',
    total_return: null,
    buy_and_hold_return: null,
    sharpe: null,
    max_drawdown: null,
    win_rate: null,
    num_trades: null,
    equity: null,
    error: 'The model expects 25 × 23 but got 25 × 101.',
    ...fields,
  });

// GET /api/v1/backtests (any query unless `query` is given) with this page.
export const backtestsAnswer = (page: Partial<BacktestsPage>, query = ''): Record<string, Answer> => {
  const backtests = page.backtests ?? [];
  return {
    [`GET /api/v1/backtests${query}`]: {
      status: 200,
      body: {
        backtests,
        total: backtests.length,
        page: 1,
        pages: 1,
        counts: { all: backtests.length, starred: 0, failed: 0 },
        models: [],
        ...page,
      },
    },
  };
};

// GET /api/v1/dashboard of an account; without fields, one without backtests.
export const dashboardAnswer = (dashboard: Partial<Dashboard> = {}): Record<string, Answer> => ({
  'GET /api/v1/dashboard': {
    status: 200,
    body: {
      counts: { all: 0, starred: 0, failed: 0 },
      last: [],
      best: { sharpe: [], total_return: [], max_drawdown: [], win_rate: [] },
      starred: { backtests: [], total: 0 },
      ...dashboard,
    },
  },
});

// GET /api/v1/models with these models; the storage they take is the sum of their sizes.
export const modelsAnswer = (list: Model[]): Record<string, Answer> => ({
  'GET /api/v1/models': {
    status: 200,
    body: {
      models: list,
      usage: {
        used_bytes: list.reduce((sum, item) => sum + item.size_bytes, 0),
        quota_bytes: 100 * MB,
        file_max_bytes: 50 * MB,
      },
    },
  },
});

// A manifest of raw candles: the five prices of the last `rows` candles, nothing scaled.
export const manifest = (rows = 25, fields: Partial<Manifest> = {}): Manifest => ({
  version: 1,
  input: {
    timeframe: '30m',
    session: 'regular',
    representation: { id: 'candles@1' },
    features: ['open', 'high', 'low', 'close', 'volume'].map((name) => ({
      name,
      id: `${name}@1`,
      norm: { id: 'none@1' },
    })),
    window: { length: rows, last: 'closed' },
  },
  signature: {
    input: { shape: [rows, 5], dtype: 'float32' },
    output: { shape: [1], dtype: 'float32', meaning: 'score' },
  },
  ...fields,
});

// The summary the API gives a model with that manifest.
export const rawCandlesSummary = (rows = 25): NonNullable<Model['manifest_summary']> => ({
  representation: { id: 'candles@1', params: {} },
  timeframe: '30m',
  session: 'regular',
  input_shape: [rows, 5],
  warm_up: { candles: rows - 1, kind: 'exact' },
});

const block = (
  id: string,
  step: CatalogBlock['step'],
  description: string,
  group: string | null = null,
): CatalogBlock => ({
  id,
  step,
  description,
  group,
  params: [],
  warm_up: '0',
});

// GET /api/v1/preprocessing/catalog as the API has it so far.
export const CATALOG: Catalog = {
  blocks: [
    block('candles@1', 'representation', 'Every candle a row'),
    ...['open', 'high', 'low', 'close', 'volume'].map((name) =>
      block(`${name}@1`, 'feature', `The ${name} of the candle`, 'Candle'),
    ),
    block('none@1', 'normalization', 'No normalization: the values as they are'),
    block('window@1', 'window', 'The last rows up to each row make one sample'),
  ],
};

export const catalogAnswer = { 'GET /api/v1/preprocessing/catalog': { status: 200, body: CATALOG } };

// The data of the worked example of docs/engine.md: AAPL, 30-minute candles.
const EXAMPLE_DATA: BacktestDocument['data'] = {
  instrument: {
    id: 'stock:AAPL',
    market: 'stock',
    symbol: 'AAPL',
    name: 'Apple Inc.',
    exchange: 'NASDAQ',
    source: 'alpaca',
    feed: 'sip',
    currency: 'USD',
  },
  timeframe: '30m',
  timeframe_seconds: 1800,
  session: 'regular',
  period: { start: 1717372800, end: 1717459200 },
  loaded: { start: 1717081200, end: 1717430400, count: 29, fingerprint: 'sha256:3b1f', gaps_total: 0 },
  changed: false,
  warm_up: { candles: 24, kind: 'exact', dropped: 24 },
  rows: { first_ts: 1717421400, last_ts: 1717428600, count: 5 },
  predictions: { count: 5, sha256: 'e1aa90d2' },
  align: { predictions_without_candle: 0, candles_without_prediction: 0 },
};

// The results of the worked example of docs/engine.md.
const EXAMPLE_RESULTS: NonNullable<BacktestDocument['results']> = {
  stats: {
    total_return: -0.05195,
    max_drawdown: -0.060224,
    sharpe: null,
    sortino: null,
    num_trades: 1,
    win_rate: 0,
    avg_trade_return: -0.05195,
    best_trade_return: -0.05195,
    worst_trade_return: -0.05195,
    exposure: 0.2,
    fees_paid: 0.00195,
    buy_and_hold_return: -0.02,
  },
  benchmarks: { bh: { total_return: -0.02 } },
  series: {
    resolution: 'candle',
    count: 5,
    columns: {
      ts: [1717421400, 1717423200, 1717425000, 1717426800, 1717428600],
      close: [100, 101, 103, 97, 98],
      prediction: [0.01, 0.3, 0.2, 0.1, 0.4],
      signal: [0, 1, 1, 1, 1],
      position: [0, 0, 1, 0, 0],
      equity: [1, 1, 1.008804, 0.94805, 0.94805],
      drawdown: [0, 0, 0, -0.060224, -0.060224],
    },
    benchmarks: { bh: [1, 1.01, 1.03, 0.97, 0.98] },
  },
  trades: {
    count: 1,
    total: 1,
    complete: true,
    columns: {
      signal_ts: [1717423200],
      open_ts: [1717425000],
      close_ts: [1717426800],
      side: [1],
      open_price: [102],
      close_price: [96.9],
      stop_price: [96.9],
      exit_reason: ['stop'],
      return: [-0.05195],
      fees: [0.00195],
      slippage_cost: [0],
      candles_held: [1],
    },
  },
};

// The worked example of docs/engine.md as the result document of #12: AAPL, 30-minute candles,
// one long trade stopped out, 5 % stop and 10 bps fees, against buy and hold.
export const backtestDocument = (fields: Partial<BacktestDocument> = {}): BacktestDocument => ({
  version: 1,
  backtest: {
    id: 'bt_7f3k2m9qx4',
    number: 12,
    status: 'finished',
    created_at: '2026-10-17T14:05:00Z',
    finished_at: '2026-10-17T14:07:42Z',
    duration_s: 162,
    error: null,
  },
  software: {
    engine_version: '0.3.0',
    app_version: 'dev',
    runner: { format: 'keras', keras: '3.15.1', backend: 'jax 0.7.2' },
  },
  model: {
    id: 'mdl_7f3k2m9qx4',
    name: 'raw-candles.keras',
    framework: 'keras',
    sha256: '9b1e04c7',
    size_bytes: 48213,
    manifest: manifest(),
  },
  data: EXAMPLE_DATA,
  decision: {
    calibrate: null,
    ema: null,
    rule: { name: 'threshold', limit: 0.05 },
    long_only: false,
    confirm: null,
    min_hold: null,
  },
  execution: { fill: 'next_open', stop_loss: 0.05, drawdown_stop: null, fee_bps: 10, slippage_bps: 0 },
  benchmarks: [{ id: 'bh', kind: 'buy_and_hold', instrument: 'stock:AAPL' }],
  metrics: { periods_per_year: 252, risk_free_rate: 0 },
  results: EXAMPLE_RESULTS,
  ...fields,
});

// The same document while it runs: the setup only.
export const runningDocument = (): BacktestDocument => {
  const doc = backtestDocument();
  return {
    ...doc,
    backtest: { ...doc.backtest, status: 'running', finished_at: null, duration_s: null },
    software: { ...doc.software, runner: null },
    data: { ...doc.data, loaded: null, changed: null, rows: null, predictions: null, align: null },
    results: null,
  };
};
