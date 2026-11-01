import { Link, useRouterState } from '@tanstack/react-router';
import { ChevronDown, Scale, X } from 'lucide-react';
import { useEffect } from 'react';

import { Button } from '@/components/ui/button';

import { useOpenedFrom } from '../useOpenedFrom';
import { SLOT_COLORS } from './colors';
import { CompareLink } from './CompareLink';
import {
  SLOTS,
  claimCompare,
  clearCompare,
  hideComparePanel,
  removeFromCompare,
  useCompareSelection,
  type ComparePick,
} from './selection';

const COUNT = 'font-mono text-xs font-medium text-muted-foreground';

function Chosen({ pick, color }: { pick: ComparePick; color: string }) {
  const from = useOpenedFrom();
  return (
    <div className="flex h-[30px] items-center gap-2 rounded-md bg-muted pr-0.5 pl-2 text-[13px] whitespace-nowrap">
      <span aria-hidden="true" className="size-2 flex-none rounded-full" style={{ background: color }} />
      <Link
        to="/backtests/$backtestId"
        params={{ backtestId: pick.id }}
        state={from && { from }}
        className="font-mono font-medium outline-none hover:text-primary focus-visible:text-primary"
      >
        #{pick.number}
      </Link>
      <span className="min-w-0 flex-1 overflow-hidden text-ellipsis">{pick.symbol}</span>
      <button
        type="button"
        aria-label={`Remove backtest ${pick.number} from compare`}
        title="Remove from compare"
        onClick={() => {
          removeFromCompare(pick.id);
        }}
        className="grid size-6 flex-none cursor-pointer place-items-center rounded-md text-muted-foreground outline-none hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <X aria-hidden="true" className="size-3" />
      </button>
    </div>
  );
}

// The panel folded: what reopens it.
function FoldedPanel({ count }: { count: string }) {
  return (
    <Button
      type="button"
      variant="outline"
      onClick={() => {
        hideComparePanel(false);
      }}
      className="fixed right-6 bottom-6 z-40 h-10 bg-background shadow-[0_6px_24px_var(--shadow)] max-[480px]:right-4 max-[480px]:bottom-4"
    >
      <Scale aria-hidden="true" />
      Compare <span className={COUNT}>{count}</span>
    </Button>
  );
}

// Compare with the count, Clear and the fold.
function PanelHead({ count }: { count: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[13px] font-semibold">
        Compare <span className={COUNT}>{count}</span>
      </span>
      <span className="inline-flex items-center gap-1">
        <button
          type="button"
          onClick={clearCompare}
          className="cursor-pointer text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground"
        >
          Clear
        </button>
        <button
          type="button"
          aria-label="Hide"
          title="Hide"
          onClick={() => {
            hideComparePanel(true);
          }}
          className="grid size-6 cursor-pointer place-items-center rounded-md text-muted-foreground outline-none hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <ChevronDown aria-hidden="true" className="size-3.5" />
        </button>
      </span>
    </div>
  );
}

// The backtests chosen to compare, in the bottom right corner of the signed-in pages while there
// are any: their slots one under the other, Clear, a fold to a small button, and Compare from 2.
export function ComparePanel({ userId }: { userId: string }) {
  const { slots, picks, hidden } = useCompareSelection();
  const comparing = useRouterState({ select: ({ location }) => location.pathname === '/backtests/compare' });
  useEffect(() => {
    claimCompare(userId);
  }, [userId]);
  if (picks.length === 0 || comparing) return null;
  const count = `${picks.length} of ${SLOTS}`;
  if (hidden) return <FoldedPanel count={count} />;
  return (
    <aside
      aria-label="Compare"
      className="fixed right-6 bottom-6 z-40 flex w-60 flex-col gap-2.5 rounded-[10px] border bg-background p-3 shadow-[0_6px_24px_var(--shadow)] max-[480px]:right-4 max-[480px]:bottom-4"
    >
      <PanelHead count={count} />
      <div className="flex flex-col gap-1">
        {slots.map((slot, index) =>
          slot ? (
            <Chosen key={slot.id} pick={slot} color={SLOT_COLORS[index] ?? 'currentColor'} />
          ) : (
            // eslint-disable-next-line react/no-array-index-key -- an empty slot has only its place
            <div key={index} aria-hidden="true" className="h-[30px] rounded-md border border-dashed" />
          ),
        )}
      </div>
      {picks.length < 2 ? (
        <Button type="button" disabled title="Add at least 2 backtests" className="w-full">
          Compare
        </Button>
      ) : (
        <Button asChild className="w-full">
          <CompareLink>Compare</CompareLink>
        </Button>
      )}
    </aside>
  );
}
