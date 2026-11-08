import { Link, useRouterState } from '@tanstack/react-router';
import { ChevronDown, Scale, X } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { AuthorAvatar } from '../experiments/AuthorAvatar';
import { BacktestNameLines } from '../parts';
import { useOpenedFrom } from '../useOpenedFrom';
import { SLOT_COLORS } from './colors';
import { CompareLink } from './CompareLink';
import {
  SLOTS,
  claimCompare,
  clearCompare,
  hideComparePanel,
  pickDescription,
  pickName,
  removeFromCompare,
  useCompareSelection,
  type ComparePick,
} from './selection';
import { usePanelPicks } from './unpublished';

const COUNT = 'font-mono text-xs font-medium text-muted-foreground';
const DISMISS =
  'grid size-6 flex-none cursor-pointer place-items-center rounded-md text-muted-foreground outline-none hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50';

// A slot of two lines, 44 px high (backtest-names, .tray-r).
const SLOT = 'flex h-11 items-center gap-2 rounded-md whitespace-nowrap';

// The name of a chosen backtest, which opens it.
function Opener({ id, children }: { id: string; children: ReactNode }) {
  const from = useOpenedFrom();
  return (
    <Link
      to="/backtests/$backtestId"
      params={{ backtestId: id }}
      state={from && { from }}
      className="flex max-w-full min-w-0 outline-none hover:text-primary focus-visible:text-primary"
    >
      {children}
    </Link>
  );
}

// A chosen backtest: its colour, its name opening it over what it ran on, another account's
// author by initials (backtest-names, .tray-r), and the cross.
function Chosen({ pick, color }: { pick: ComparePick; color: string }) {
  const name = pickName(pick);
  return (
    <div className={cn(SLOT, 'bg-muted pr-0.5 pl-2 text-[13px]')}>
      <span
        aria-hidden="true"
        className="mt-[11px] size-2 flex-none self-start rounded-full"
        style={{ background: color }}
      />
      <BacktestNameLines
        name={name}
        description={pickDescription(pick)}
        nameClassName="text-[13px]/[18px] font-semibold"
        descriptionClassName="text-[11px]/[15px]"
        wrap={(shown) => <Opener id={pick.id}>{shown}</Opener>}
      />
      {pick.author !== undefined && <AuthorAvatar name={pick.author} size="xxs" />}
      <button
        type="button"
        aria-label={`Remove ${name} from compare`}
        title="Remove from compare"
        onClick={() => {
          removeFromCompare(pick.id);
        }}
        className={DISMISS}
      >
        <X aria-hidden="true" className="size-3" />
      </button>
    </div>
  );
}

// A slot whose backtest its author unpublished (.tray-r.gone): dashed in the warning colour, the
// author's initials, "<name> was unpublished" over what it ran on, and the cross that dismisses it.
function Unpublished({ pick }: { pick: ComparePick }) {
  return (
    <div
      title={`${pick.author ?? 'Its author'} unpublished it`}
      className={cn(
        SLOT,
        'border border-dashed border-[color-mix(in_srgb,var(--warn)_45%,var(--rule))] pr-0.5 pl-1.5 text-xs text-muted-foreground',
      )}
    >
      {pick.author !== undefined && (
        <span className="mt-[7px] flex self-start">
          <AuthorAvatar name={pick.author} size="xxs" />
        </span>
      )}
      <BacktestNameLines
        name={`${pickName(pick)} was unpublished`}
        description={pickDescription(pick)}
        nameClassName="text-[12.5px]/[17px] font-medium text-foreground"
        descriptionClassName="text-[11px]/[15px]"
      />
      <button
        type="button"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={() => {
          removeFromCompare(pick.id);
        }}
        className={DISMISS}
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
  const { slots, picks, gone, hidden } = useCompareSelection();
  const comparing = useRouterState({ select: ({ location }) => location.pathname === '/backtests/compare' });
  useEffect(() => {
    claimCompare(userId);
  }, [userId]);
  usePanelPicks(!comparing);
  if ((picks.length === 0 && gone.length === 0) || comparing) return null;
  const count = `${picks.length} of ${SLOTS}`;
  if (hidden) return <FoldedPanel count={count} />;
  return (
    <aside
      aria-label="Compare"
      className="fixed right-6 bottom-6 z-40 flex w-66 flex-col gap-2.5 rounded-[10px] border bg-background p-3 shadow-[0_6px_24px_var(--shadow)] max-[480px]:right-4 max-[480px]:bottom-4"
    >
      <PanelHead count={count} />
      <div className="flex flex-col gap-1">
        {slots.map((slot, index) =>
          slot?.gone ? (
            <Unpublished key={slot.id} pick={slot} />
          ) : slot ? (
            <Chosen key={slot.id} pick={slot} color={SLOT_COLORS[index] ?? 'currentColor'} />
          ) : (
            // eslint-disable-next-line react/no-array-index-key -- an empty slot has only its place
            <div key={index} aria-hidden="true" className="h-11 rounded-md border border-dashed" />
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
