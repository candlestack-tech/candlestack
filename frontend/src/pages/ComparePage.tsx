import { useQueries } from '@tanstack/react-query';
import { useCanGoBack, useNavigate, useRouter, useRouterState } from '@tanstack/react-router';
import { Scale } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { ApiError, backtestQueryOptions, type BacktestDocument } from '@/api';
import { ErrorNote } from '@/components/ErrorNote';
import { PageTitle } from '@/components/PageTitle';
import { Button } from '@/components/ui/button';
import {
  CompareCharts,
  ComparePicker,
  CompareTable,
  SLOTS,
  SLOT_COLORS,
  clearCompare,
  lineOf,
  removeFromCompare,
  useCompareSelection,
  type CompareColumn,
} from '@/features/backtests';
import { usePageTitle } from '@/lib/usePageTitle';

const isMissing = (error: unknown) => error instanceof ApiError && error.status === 404;

// The chosen backtests in the order of their slots, each with its colour. A chosen one that no
// longer exists leaves the choice.
function useColumns() {
  const { slots, picks } = useCompareSelection();
  const results = useQueries({ queries: picks.map((pick) => backtestQueryOptions(pick.id)) });
  const gone = picks.filter((_, index) => isMissing(results[index]?.error)).map((pick) => pick.id);
  const goneKey = gone.join();
  useEffect(() => {
    if (goneKey) goneKey.split(',').forEach(removeFromCompare);
  }, [goneKey]);
  const docs = new Map<string, BacktestDocument>();
  picks.forEach((pick, index) => {
    const data = results[index]?.data;
    if (data) docs.set(pick.id, data);
  });
  const columns: CompareColumn[] = slots.flatMap((slot, index) => {
    const doc = slot && docs.get(slot.id);
    return doc ? [{ doc, color: SLOT_COLORS[index] ?? 'currentColor' }] : [];
  });
  const failed = results.some((result) => result.error && !isMissing(result.error));
  return { columns, count: picks.length, failed };
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
      <p className="text-muted-foreground">Add 2 to 4 finished backtests.</p>
      <Button type="button" className="mt-1" onClick={onAdd}>
        Add backtests
      </Button>
    </section>
  );
}

const countLine = (count: number) =>
  count === 1 ? `1 of ${SLOTS} backtests. Add one more to compare` : `${count} of ${SLOTS} backtests`;

// /backtests/compare: up to four backtests side by side, on the charts and in the table.
export function ComparePage() {
  usePageTitle('Compare backtests');
  const { columns, count, failed } = useColumns();
  const [adding, setAdding] = useState(false);
  const open = () => {
    setAdding(true);
  };
  const lines = useMemo(() => columns.flatMap(({ doc, color }) => lineOf(doc, color) ?? []), [columns]);
  return (
    <>
      <Back />
      <PageTitle
        variant="workspace"
        title="Compare backtests"
        description={countLine(count)}
        action={
          count > 0 && (
            <Button type="button" variant="outline" onClick={clearCompare}>
              Clear all
            </Button>
          )
        }
      />
      {failed && <ErrorNote className="mb-3">Could not load every backtest. Try again.</ErrorNote>}
      {count === 0 ? (
        <Empty onAdd={open} />
      ) : (
        columns.length > 0 && (
          <>
            <CompareCharts lines={lines} />
            <CompareTable columns={columns} canAdd={count < SLOTS} onAdd={open} />
          </>
        )
      )}
      <ComparePicker open={adding} onOpenChange={setAdding} />
    </>
  );
}
