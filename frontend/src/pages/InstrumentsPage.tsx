import { useNavigate, useSearch } from '@tanstack/react-router';
import { useCallback, useEffect } from 'react';

import { SearchBox } from '@/components/SearchBox';
import { Filters, InstrumentFacts, InstrumentList, rememberListSearch } from '@/features/instruments';
import { usePageTitle } from '@/lib/usePageTitle';

// Every stock and crypto pair, searchable, the most traded first.
// /instruments?q=apple&market=stock&exchange=NASDAQ&sort=gainers&page=2
export function InstrumentsPage() {
  const search = useSearch({ from: '/_app/instruments' });
  const { q = '', market, exchange, sort, page = 1 } = search;
  const navigate = useNavigate({ from: '/instruments' });
  // "Instruments" on an instrument page comes back to this search.
  useEffect(() => rememberListSearch(search), [search]);
  usePageTitle(q === '' ? 'Instruments' : `“${q}” · Instruments`);

  // A new query starts at page 1; typing replaces the history entry instead of adding one per pause.
  const onSearch = useCallback(
    (next: string) =>
      void navigate({ search: (prev) => ({ ...prev, q: next || undefined, page: undefined }), replace: true }),
    [navigate],
  );

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Instruments</h1>
        <p className="mt-2 max-w-[64ch] text-sm text-muted-foreground">
          Everything you can run a backtest on: US stocks and ETFs from every exchange, and Binance spot pairs. Open one
          to see its candles.
        </p>
        <InstrumentFacts />
      </div>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <SearchBox
          query={q}
          onSearch={onSearch}
          placeholder="Symbol or name, e.g. btc or apple"
          label="Search instruments by symbol or name"
          maxLength={50}
          debounceMs={300}
        />
        <Filters market={market} exchange={exchange} sort={sort} />
      </div>
      <InstrumentList query={q} market={market} exchange={exchange} sort={sort} page={page} />
    </div>
  );
}
