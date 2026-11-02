import type { ExperimentsPage, Representation, SetupTimeframe } from '@/api';
import type { ExperimentsColumn, ExperimentsSearch } from '@/routes/experimentsSearch';

import { representationLabel } from './format';

// The rules of the chips of Experiments (public-experiments, fChip and sortDd): what each one
// offers with the count the API gives it (`facets`: over the scope and the search, whatever the
// filters), what it says once set, and the URL after a pick, which starts at page 1.

type Facets = ExperimentsPage['facets'];

export type ChipOption = {
  key: string;
  label: string;
  count: number;
  selected: boolean;
  // The instrument's id for its icon, `<market>:<symbol>`.
  icon?: string;
  apply: (search: ExperimentsSearch) => ExperimentsSearch;
};

const countOf = (list: readonly { value: string; count: number }[], value: string) =>
  list.find((each) => each.value === value)?.count ?? 0;
const sum = (list: readonly { count: number }[]) => list.reduce((total, each) => total + each.count, 0);
const fresh = (search: ExperimentsSearch, change: Partial<ExperimentsSearch>): ExperimentsSearch => ({
  ...search,
  ...change,
  page: undefined,
});

const MARKETS = [
  ['stock', 'Stocks'],
  ['crypto', 'Crypto'],
] as const;

// All markets, Stocks and Crypto; then, under "Instrument", each published one, the most published
// first. A market and an instrument exclude each other.
export function marketOptions(search: ExperimentsSearch, facets: Facets) {
  const markets: ChipOption[] = [
    {
      key: 'all',
      label: 'All markets',
      count: sum(facets.markets),
      selected: !search.market && !search.symbol,
      apply: (prev) => fresh(prev, { market: undefined, symbol: undefined }),
    },
    ...MARKETS.map(([market, label]) => ({
      key: market,
      label,
      count: countOf(facets.markets, market),
      selected: search.market === market,
      apply: (prev: ExperimentsSearch) => fresh(prev, { market, symbol: undefined }),
    })),
  ];
  const instruments: ChipOption[] = facets.instruments.map(({ market, symbol, count }) => ({
    key: `${market}:${symbol}`,
    label: symbol,
    count,
    icon: `${market}:${symbol}`,
    selected: search.symbol?.toUpperCase() === symbol.toUpperCase(),
    apply: (prev) => fresh(prev, { market: undefined, symbol }),
  }));
  return { markets, instruments };
}

export function marketValue(search: ExperimentsSearch): string | undefined {
  if (search.symbol) return search.symbol;
  return MARKETS.find(([market]) => market === search.market)?.[1];
}

const REPRESENTATIONS: readonly Representation[] = ['renko', 'kagi', 'windows'];

// Any, then Renko, Kagi and Candles, each always offered (a count of 0 is muted).
export function representationOptions(search: ExperimentsSearch, facets: Facets): ChipOption[] {
  return [
    {
      key: 'any',
      label: 'Any',
      count: sum(facets.representations),
      selected: search.representation === undefined,
      apply: (prev) => fresh(prev, { representation: undefined }),
    },
    ...REPRESENTATIONS.map((representation) => ({
      key: representation,
      label: representationLabel(representation),
      count: countOf(facets.representations, representation),
      selected: search.representation === representation,
      apply: (prev: ExperimentsSearch) => fresh(prev, { representation }),
    })),
  ];
}

export const representationValue = (search: ExperimentsSearch) =>
  search.representation && representationLabel(search.representation);

// Any, then the timeframes that were published, shortest first; the one chosen stays offered
// even when nothing has it any more.
export function timeframeOptions(search: ExperimentsSearch, facets: Facets): ChipOption[] {
  const published = facets.timeframes.map(({ value }) => value);
  const chosen = search.timeframe;
  const values = chosen && !published.includes(chosen) ? [...published, chosen] : published;
  return [
    {
      key: 'any',
      label: 'Any',
      count: sum(facets.timeframes),
      selected: chosen === undefined,
      apply: (prev) => fresh(prev, { timeframe: undefined }),
    },
    ...values.map((value) => ({
      key: value,
      label: value,
      count: countOf(facets.timeframes, value),
      selected: chosen === value,
      apply: (prev: ExperimentsSearch) => fresh(prev, { timeframe: value as SetupTimeframe }),
    })),
  ];
}

// The orders of the Sort menu, in its order, with what the first and the second way mean.
export const SORT_OPTIONS: readonly {
  column: ExperimentsColumn | undefined;
  label: string;
  down: string;
  up: string;
}[] = [
  { column: 'sharpe', label: 'Sharpe', down: 'High to low', up: 'Low to high' },
  { column: 'return', label: 'Return', down: 'High to low', up: 'Low to high' },
  { column: 'max_drawdown', label: 'Max drawdown', down: 'Smallest first', up: 'Deepest first' },
  { column: undefined, label: 'Newest', down: 'Last published first', up: 'Last published first' },
];

// What the Sort button says: "Newest", "Sharpe", "Return ↑".
export function sortValue(search: ExperimentsSearch): string {
  const label = SORT_OPTIONS.find((option) => option.column === search.sort)?.label ?? 'Newest';
  return search.sort && search.order === 'asc' ? `${label} ↑` : label;
}
