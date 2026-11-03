import type { SearchSchemaInput } from '@tanstack/react-router';

import type { BacktestsParams, SortOrder } from '@/api';

// The Backtests page keeps its search, filters, order and page in the URL, so Back returns to
// them and the request mirrors what the page shows:
// /backtests?q=aapl&show=starred&model=lstm.keras&sort=sharpe&order=asc&page=2 (show: starred,
// public or failed). Defaults are left out: all backtests, every model, newest first, page 1.
export type BacktestsSearch = BacktestsParams;

export type BacktestsColumn = NonNullable<BacktestsSearch['sort']>;

const SORTS: readonly BacktestsColumn[] = ['number', 'model', 'return', 'sharpe', 'max_drawdown', 'trades', 'when'];

const MAX_QUERY = 100; // the API's limit for `q`
const MAX_MODEL = 64; // a model's name: 60 characters and its extension

function parseText(value: unknown, max: number): string | undefined {
  if (typeof value !== 'string' && typeof value !== 'number') return undefined;
  const text = String(value).trim().slice(0, max);
  return text === '' ? undefined : text;
}

const MAX_PAGE = 100_000; // the API's limit for `page`

// A page the API takes: a larger one asks for page 100000, which the list then brings back to
// its last page, instead of a request the API refuses.
function parsePage(value: unknown): number | undefined {
  const page = Number(value);
  return Number.isSafeInteger(page) && page > 1 ? Math.min(page, MAX_PAGE) : undefined;
}

// The order a column sorts in first: down, but the model A to Z.
const firstOrder = (column: BacktestsColumn): SortOrder => (column === 'model' ? 'asc' : 'desc');

// A column and its order; an order without a column means nothing.
function parseSort(sort: unknown, order: unknown): Pick<BacktestsSearch, 'sort' | 'order'> {
  const column = SORTS.find((each) => each === sort);
  if (column === undefined) return {};
  return { sort: column, order: order === 'asc' || order === 'desc' ? order : firstOrder(column) };
}

// Unknown or broken values are dropped, so ?show=all or ?page=-3 opens the plain list. Each key
// stays in the result, undefined when dropped: the router lays the result over the raw URL values
// its parent routes pass on, and a key left out would bring the broken value back to the request.
export function validateBacktestsSearch(
  search: {
    q?: unknown;
    show?: unknown;
    model?: unknown;
    sort?: unknown;
    order?: unknown;
    page?: unknown;
  } & SearchSchemaInput,
): BacktestsSearch {
  return {
    q: parseText(search.q, MAX_QUERY),
    show: search.show === 'starred' || search.show === 'failed' || search.show === 'public' ? search.show : undefined,
    model: parseText(search.model, MAX_MODEL),
    sort: undefined,
    order: undefined,
    ...parseSort(search.sort, search.order),
    page: parsePage(search.page),
  };
}

// What a click on a column's header does (mockups/app, btSort): a new column sorts down (Model
// up, A to Z), a second click the other way, a third goes back to newest first. Like every
// change, it starts at page 1.
export function withSortBy(search: BacktestsSearch, column: BacktestsColumn): BacktestsSearch {
  const first = firstOrder(column);
  const next = { ...search, page: undefined };
  if (search.sort !== column) return { ...next, sort: column, order: first };
  if (search.order === first) return { ...next, sort: column, order: first === 'asc' ? 'desc' : 'asc' };
  return { ...next, sort: undefined, order: undefined };
}
