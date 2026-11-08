import type { SearchSchemaInput } from '@tanstack/react-router';

// The compare page keeps the backtests it compares in the URL, so a copied address, or a link an
// agent gives over MCP, opens the same comparison: /backtests/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5.
// Anything that is no backtest ID is dropped, and so are repeats and those past the page's four.
export type CompareSearch = { ids: string | undefined };

// How many backtests the page compares at most: its slots, each with its colour.
export const COMPARE_SLOTS = 4;

const BACKTEST_ID = /^bt_[a-z0-9]{1,32}$/;

// The IDs of `ids`, in their order: one comma-separated value, or the same key repeated.
function compareIdsOf(ids: unknown): string[] {
  const values = Array.isArray(ids) ? ids : [ids];
  const all = values.flatMap((value) => (typeof value === 'string' ? value.split(',') : []));
  const valid = all.map((id) => id.trim()).filter((id) => BACKTEST_ID.test(id));
  return [...new Set(valid)].slice(0, COMPARE_SLOTS);
}

// The key stays, undefined when nothing is left: the router lays the result over the raw URL values.
export function validateCompareSearch(search: { ids?: unknown } & SearchSchemaInput): CompareSearch {
  const ids = compareIdsOf(search.ids);
  return { ids: ids.length > 0 ? ids.join(',') : undefined };
}
