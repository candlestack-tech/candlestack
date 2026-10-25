import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect } from 'react';

import type { BacktestsPage } from '@/api';
import { Pager } from '@/components/Pager';
import { SearchBox } from '@/components/SearchBox';
import { withSortBy, type BacktestsSearch } from '@/routes/backtestsSearch';

import { BacktestFilters } from './BacktestFilters';
import { BacktestsTable } from './BacktestsTable';
import { useStarBacktest } from './useBacktests';

const NO_FILTERS: Pick<BacktestsPage, 'counts' | 'models'> = { counts: { all: 0, starred: 0, failed: 0 }, models: [] };

// An old or shared link can point past the end (?page=9 of 2): once the pages are known, the page
// goes to the last one, in place of the broken entry in the history.
function useClampPage(page: number, pages: number | undefined) {
  const navigate = useNavigate({ from: '/backtests' });
  useEffect(() => {
    if (pages === undefined || page <= pages) return;
    void navigate({ search: (prev) => ({ ...prev, page: pages > 1 ? pages : undefined }), replace: true });
  }, [page, pages, navigate]);
}

type Props = { search: BacktestsSearch; data: BacktestsPage | undefined; loading: boolean };

// The search field, up to 384 px wide, and the Filters button beside it (.bt-tools).
function Tools({ search, data }: Omit<Props, 'loading'>) {
  const navigate = useNavigate({ from: '/backtests' });
  // A new search starts at page 1; typing replaces the history entry instead of adding one per pause.
  const onSearch = useCallback(
    (q: string) =>
      void navigate({ search: (prev) => ({ ...prev, q: q || undefined, page: undefined }), replace: true }),
    [navigate],
  );
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2.5">
      <SearchBox
        query={search.q ?? ''}
        onSearch={onSearch}
        placeholder="Number, instrument or model, e.g. aapl"
        label="Search backtests"
        maxLength={100}
        debounceMs={200}
        className="w-auto max-w-96 flex-[1_1_260px]"
      />
      <BacktestFilters search={search} page={data ?? NO_FILTERS} />
    </div>
  );
}

// The search, the Filters, the table and the pager (mockups/app, ddRuns). Everything is in the
// URL and the API searches, filters, sorts and pages; while the next page loads the previous one
// stays, and the search field keeps its focus and its text.
export function BacktestsList({ search, data, loading }: Props) {
  const navigate = useNavigate({ from: '/backtests' });
  const star = useStarBacktest();
  const page = search.page ?? 1;
  useClampPage(page, loading ? undefined : data?.pages);
  return (
    <>
      <Tools search={search} data={data} />
      {data && (
        <div className="overflow-hidden rounded-lg border bg-card">
          <BacktestsTable
            backtests={data.backtests}
            empty={data.counts.all === 0 ? 'No backtests yet' : 'No backtests match'}
            search={search}
            onSort={(column) => void navigate({ search: (prev) => withSortBy(prev, column) })}
            onStar={(backtest) => star.mutate(backtest)}
          />
        </div>
      )}
      {data && <Pager from="/backtests" page={page} hasNext={page < data.pages} pages={data.pages} loading={loading} />}
    </>
  );
}
