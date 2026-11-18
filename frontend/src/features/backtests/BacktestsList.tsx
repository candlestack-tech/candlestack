import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect, useState } from 'react';

import type { Backtest, BacktestsPage } from '@/api';
import { Pager } from '@/components/Pager';
import { SearchBox } from '@/components/SearchBox';
import { useToast } from '@/components/Toast';
import { plural } from '@/lib/plural';
import { withSortBy, type BacktestsSearch } from '@/routes/backtestsSearch';

import { BacktestFilters } from './BacktestFilters';
import { BacktestsTable } from './BacktestsTable';
import { DeleteButton, DeleteDialog } from './DeleteDialog';
import { useStarBacktest } from './useBacktests';

const NO_FILTERS: Pick<BacktestsPage, 'counts' | 'models'> = {
  counts: { all: 0, starred: 0, failed: 0, public: 0 },
  models: [],
};

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

// The search field, up to 384 px wide, and the Filters button beside it (.bt-tools); at the right,
// Delete with how many rows are chosen, while any are.
function Tools({ search, data, chosen, onDelete }: Omit<Props, 'loading'> & { chosen: number; onDelete: () => void }) {
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
        placeholder="Name, instrument or model"
        label="Search backtests"
        maxLength={100}
        debounceMs={200}
        className="w-auto max-w-96 flex-[1_1_260px]"
      />
      <BacktestFilters search={search} page={data ?? NO_FILTERS} />
      {chosen > 0 && (
        <DeleteButton className="ml-auto" onClick={onDelete}>
          Delete {chosen}
        </DeleteButton>
      )}
    </div>
  );
}

// The rows chosen to delete, by ID. Only those on the page shown count, so another page or search
// never deletes what is out of sight.
function useSelection(backtests: readonly Backtest[]) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const select = useCallback((ids: readonly string[], on: boolean) => {
    setSelected((last) => {
      const next = new Set(last);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }, []);
  const chosen = backtests.filter((backtest) => selected.has(backtest.id));
  return { selected, select, chosen, clear: () => setSelected(new Set()) };
}

// Delete 3 backtests? over the list, and the toast once they are gone. The dialog keeps the rows
// chosen when it opened, so it reads the same while the list refreshes and while it fades out.
function useBulkDelete(chosen: readonly Backtest[], clear: () => void) {
  const [open, setOpen] = useState(false);
  const [doomed, setDoomed] = useState<readonly Backtest[]>([]);
  const { toast, show } = useToast();
  const [only] = doomed;
  const dialog = (
    <DeleteDialog
      backtests={doomed}
      open={open}
      onOpenChange={setOpen}
      onDeleted={(gone) => {
        setOpen(false);
        clear();
        show(
          doomed.length === 1 && only ? `${only.name} was deleted` : `${plural(gone.length, 'backtest')} were deleted`,
        );
      }}
    />
  );
  const start = () => {
    setDoomed(chosen);
    setOpen(true);
  };
  return { start, dialog, toast };
}

// The search, the Filters, the table and the pager (mockups/app, ddRuns). Everything is in the
// URL and the API searches, filters, sorts and pages; while the next page loads the previous one
// stays, and the search field keeps its focus and its text. Rows chosen with their checkboxes are
// deleted at once (POST /api/v1/backtests/delete) after the same confirm as a backtest's page.
export function BacktestsList({ search, data, loading }: Props) {
  const navigate = useNavigate({ from: '/backtests' });
  const star = useStarBacktest();
  const page = search.page ?? 1;
  useClampPage(page, loading ? undefined : data?.pages);
  const selection = useSelection(data?.backtests ?? []);
  const bulk = useBulkDelete(selection.chosen, selection.clear);
  return (
    <>
      <Tools search={search} data={data} chosen={selection.chosen.length} onDelete={bulk.start} />
      {data && (
        <div className="overflow-hidden rounded-lg border bg-card">
          <BacktestsTable
            backtests={data.backtests}
            empty={data.counts.all === 0 ? 'No backtests yet' : 'No backtests match'}
            search={search}
            onSort={(column) => void navigate({ search: (prev) => withSortBy(prev, column) })}
            onStar={(backtest) => star.mutate(backtest)}
            selected={selection.selected}
            onSelect={selection.select}
          />
        </div>
      )}
      {data && <Pager from="/backtests" page={page} hasNext={page < data.pages} pages={data.pages} loading={loading} />}
      {bulk.dialog}
      {bulk.toast}
    </>
  );
}
