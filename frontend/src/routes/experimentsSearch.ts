import type { SearchSchemaInput } from '@tanstack/react-router';

import type { ExperimentsParams, Representation, SetupTimeframe, SortOrder } from '@/api';

// Experiments keeps its search, whose, filters, order and the table's page in the URL, so Back
// returns to them and a link can be shared:
// /experiments?q=nvda&mine=true&author=usr_7f3k2m9qx4&market=crypto&representation=renko&timeframe=1h&sort=sharpe&order=asc&page=2.
// Defaults are left out: everyone's, every market, newest first, page 1. A market or an instrument
// (`symbol`), never both: the Market chip picks one of them.
export type ExperimentsSearch = ExperimentsParams;

export type ExperimentsColumn = NonNullable<ExperimentsSearch['sort']>;

const SORTS: readonly ExperimentsColumn[] = ['return', 'sharpe', 'max_drawdown'];
const REPRESENTATIONS: readonly Representation[] = ['renko', 'kagi', 'windows'];
const TIMEFRAMES: readonly SetupTimeframe[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

const MAX_QUERY = 100; // the API's limit for `q`
const MAX_ID = 32; // the API's limit for `author` and `symbol`
const MAX_PAGE = 100_000; // the API's limit for `page`

function parseText(value: unknown, max: number): string | undefined {
  if (typeof value !== 'string' && typeof value !== 'number') return undefined;
  const text = String(value).trim().slice(0, max);
  return text === '' ? undefined : text;
}

function parsePage(value: unknown): number | undefined {
  const page = Number(value);
  return Number.isSafeInteger(page) && page > 1 ? Math.min(page, MAX_PAGE) : undefined;
}

const oneOf = <T>(list: readonly T[], value: unknown): T | undefined => list.find((each) => each === value);

// A column and its order; an order without a column means nothing. Every column sorts down
// first: the best Sharpe and return, the smallest fall.
function parseSort(sort: unknown, order: unknown): Pick<ExperimentsSearch, 'sort' | 'order'> {
  const column = oneOf(SORTS, sort);
  if (column === undefined) return {};
  return { sort: column, order: order === 'asc' ? 'asc' : 'desc' };
}

// An instrument wins over a market: the chip shows one of them.
function parseMarket(market: unknown, symbol: unknown): Pick<ExperimentsSearch, 'market' | 'symbol'> {
  const instrument = parseText(symbol, MAX_ID)?.toUpperCase();
  if (instrument) return { symbol: instrument };
  return { market: market === 'stock' || market === 'crypto' ? market : undefined };
}

// Unknown or broken values are dropped, and every key stays in the result, undefined when
// dropped, so the router's raw value never reaches the request (as validateBacktestsSearch).
export function validateExperimentsSearch(
  search: {
    q?: unknown;
    mine?: unknown;
    author?: unknown;
    market?: unknown;
    symbol?: unknown;
    representation?: unknown;
    timeframe?: unknown;
    sort?: unknown;
    order?: unknown;
    page?: unknown;
  } & SearchSchemaInput,
): ExperimentsSearch {
  return {
    q: parseText(search.q, MAX_QUERY),
    mine: search.mine === true || search.mine === 'true' ? true : undefined,
    author: parseText(search.author, MAX_ID),
    market: undefined,
    symbol: undefined,
    ...parseMarket(search.market, search.symbol),
    representation: oneOf(REPRESENTATIONS, search.representation),
    timeframe: oneOf(TIMEFRAMES, search.timeframe),
    sort: undefined,
    order: undefined,
    ...parseSort(search.sort, search.order),
    page: parsePage(search.page),
  };
}

// What a click on a column's header does (as on the Backtests page): a new column sorts down, a
// second click up, a third goes back to newest first. Like every change, it starts at page 1.
export function withSortBy(search: ExperimentsSearch, column: ExperimentsColumn | undefined): ExperimentsSearch {
  const next = { ...search, page: undefined };
  if (column === undefined) return { ...next, sort: undefined, order: undefined };
  if (search.sort !== column) return { ...next, sort: column, order: 'desc' };
  if (search.order !== 'asc') return { ...next, sort: column, order: 'asc' };
  return { ...next, sort: undefined, order: undefined };
}

// A pick of the Sort menu: its column from the top, newest first without one.
export function withSortPick(search: ExperimentsSearch, column: ExperimentsColumn | undefined): ExperimentsSearch {
  const order: SortOrder | undefined = column && 'desc';
  return { ...search, sort: column, order, page: undefined };
}

// Whether a search or a filter narrows the list (All and Mine do not).
export const isFiltered = (search: ExperimentsSearch) =>
  Boolean(search.q || search.author || search.market || search.symbol || search.representation || search.timeframe);

// Clear filters, and Clear search and filters: All or Mine and the order stay.
export const withoutFilters = (search: ExperimentsSearch): ExperimentsSearch => ({
  ...search,
  q: undefined,
  author: undefined,
  market: undefined,
  symbol: undefined,
  representation: undefined,
  timeframe: undefined,
  page: undefined,
});
