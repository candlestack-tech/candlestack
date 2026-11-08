import { useQuery } from '@tanstack/react-query';
import { useCanGoBack, useNavigate, useRouter, useRouterState, useSearch } from '@tanstack/react-router';
import { Scale, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { compareQueryOptions, type CompareMetric } from '@/api';
import { ErrorNote } from '@/components/ErrorNote';
import { PageTitle } from '@/components/PageTitle';
import { Button } from '@/components/ui/button';
import {
  AuthorAvatar,
  CompareCharts,
  ComparePicker,
  CompareTable,
  SLOTS,
  SLOT_COLORS,
  clearCompare,
  comparedIds,
  isMissing,
  lineOf,
  pickName,
  removeFromCompare,
  useComparedNames,
  useCompareSelection,
  useLeaveCompared,
  type CompareColumn,
  type ComparePick,
} from '@/features/backtests';
import { usePageTitle } from '@/lib/usePageTitle';

// The chosen backtests in the order of their slots, each with its colour, as the server compares
// them (GET /api/v1/compare). While a new choice loads, the last answer shows those still chosen. A
// chosen one that no longer exists leaves the choice; another account's that was unpublished says
// so (leaveCompare).
function useColumns() {
  const { slots, picks, full } = useCompareSelection();
  const { data, error } = useQuery(compareQueryOptions(picks.map((pick) => pick.id)));
  useLeaveCompared(error);
  const backtests = data?.backtests ?? [];
  useComparedNames(backtests);
  const columns: CompareColumn[] = slots.flatMap((slot, color) => {
    const index = slot && !slot.gone ? backtests.findIndex((backtest) => backtest.id === slot.id) : -1;
    const backtest = backtests[index];
    return backtest ? [{ backtest, index, color: SLOT_COLORS[color] ?? 'currentColor' }] : [];
  });
  const failed = error !== null && !isMissing(error);
  return { columns, metrics: data?.metrics ?? [], times: data?.times ?? [], picks, full, failed };
}

// The URL names the backtests compared (?ids=bt_12,bt_11), so a copied address opens the same
// comparison: every change of the choice, here or in another tab, writes it in place, keeping the
// back link's page.
function useComparedInUrl(picks: readonly ComparePick[]) {
  const { ids } = useSearch({ from: '/_app/backtests/compare' });
  const navigate = useNavigate();
  const chosen = comparedIds(picks) || undefined;
  useEffect(() => {
    if (chosen === ids) return;
    void navigate({
      to: '/backtests/compare',
      search: { ids: chosen },
      state: (state) => ({ ...state, comparedInUrl: true }),
      replace: true,
      resetScroll: false,
    });
  }, [chosen, ids, navigate]);
}

// "← Dashboard": back where the page was opened from, the Backtests page when it was opened
// directly.
function Back() {
  const router = useRouter();
  const navigate = useNavigate();
  const canGoBack = useCanGoBack();
  const from = useRouterState({ select: (state) => state.location.state.comparedFrom });
  return (
    <button
      type="button"
      onClick={() => {
        if (canGoBack) router.history.back();
        else void navigate({ to: '/backtests' });
      }}
      className="mb-3.5 inline-flex cursor-pointer items-center gap-1.5 text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground"
    >
      ← {canGoBack ? (from ?? 'Back') : 'Backtests'}
    </button>
  );
}

function Empty({ onAdd }: { onAdd: () => void }) {
  return (
    <section className="flex flex-col items-center gap-3 rounded-lg border bg-background px-6 py-14 text-center">
      <span className="grid size-11 place-items-center rounded-[10px] bg-accent-soft text-primary">
        <Scale aria-hidden="true" className="size-[22px]" />
      </span>
      <h2 className="text-lg font-semibold">Nothing to compare yet</h2>
      <p className="text-muted-foreground">Add 2 to 4 finished backtests, yours or from Experiments.</p>
      <Button type="button" className="mt-1" onClick={onAdd}>
        Add backtests
      </Button>
    </section>
  );
}

// Another account's backtest that its author unpublished, until the user dismisses it
// (public-experiments, .cmp-note).
function UnpublishedNote({ pick }: { pick: ComparePick }) {
  return (
    <div className="mb-4 flex items-center gap-2.5 rounded-lg border border-dashed border-[color-mix(in_srgb,var(--warn)_45%,var(--rule))] bg-background py-2 pr-2 pl-3 text-[13px] text-muted-foreground">
      {pick.author !== undefined && <AuthorAvatar name={pick.author} size="md" />}
      <span className="min-w-0 flex-1">
        <b className="font-medium text-foreground">{pick.author}</b> unpublished{' '}
        <b className="font-medium text-foreground">{pickName(pick)}</b>, so it left your Compare.
      </span>
      <button
        type="button"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={() => {
          removeFromCompare(pick.id);
        }}
        className="grid size-7 flex-none cursor-pointer place-items-center rounded-md text-muted-foreground outline-none hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <X aria-hidden="true" className="size-4" />
      </button>
    </div>
  );
}

type ComparedProps = {
  columns: CompareColumn[];
  metrics: readonly CompareMetric[];
  times: readonly number[];
  canAdd: boolean;
  onAdd: () => void;
};

// The charts and the table, once the comparison is in.
function Compared({ columns, metrics, times, canAdd, onAdd }: ComparedProps) {
  const lines = useMemo(
    () => columns.flatMap(({ backtest, color }) => lineOf(backtest, times, color) ?? []),
    [columns, times],
  );
  if (columns.length === 0) return null;
  return (
    <>
      <CompareCharts lines={lines} />
      <CompareTable columns={columns} metrics={metrics} canAdd={canAdd} onAdd={onAdd} />
    </>
  );
}

// "4 of 4 backtests · 2 yours, 2 from Experiments"; with one, the way on.
function countLine(picks: readonly ComparePick[]): string {
  const count = picks.length;
  const theirs = picks.filter((pick) => pick.author !== undefined).length;
  const mix = theirs > 0 ? ` · ${count - theirs} yours, ${theirs} from Experiments` : '';
  return `${count} of ${SLOTS} backtests${mix}${count === 1 ? '. Add one more to compare' : ''}`;
}

// /backtests/compare: up to four backtests side by side, on the charts and in the table, the
// user's and those of others from Experiments.
export function ComparePage() {
  usePageTitle('Compare backtests');
  const { columns, metrics, times, picks, full, failed } = useColumns();
  const { gone } = useCompareSelection();
  useComparedInUrl(picks);
  const [adding, setAdding] = useState(false);
  const open = () => {
    setAdding(true);
  };
  const count = picks.length;
  return (
    <>
      <Back />
      <PageTitle
        variant="workspace"
        title="Compare backtests"
        description={countLine(picks)}
        action={
          count + gone.length > 0 && (
            <Button type="button" variant="outline" onClick={clearCompare}>
              Clear all
            </Button>
          )
        }
      />
      {gone.map((pick) => (
        <UnpublishedNote key={pick.id} pick={pick} />
      ))}
      {failed && <ErrorNote className="mb-3">Could not load every backtest. Try again.</ErrorNote>}
      {count === 0 ? (
        <Empty onAdd={open} />
      ) : (
        <Compared columns={columns} metrics={metrics} times={times} canAdd={!full} onAdd={open} />
      )}
      <ComparePicker open={adding} onOpenChange={setAdding} />
    </>
  );
}
