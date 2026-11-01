import { Scale } from 'lucide-react';

import type { BacktestStatus } from '@/api';
import { Button } from '@/components/ui/button';
import { BUTTON_SIZES } from '@/components/buttonStyles';
import { cn } from '@/lib/utils';

import { addToCompare, removeFromCompare, useCompareSelection, type ComparePick } from './selection';

type Target = ComparePick & { status: BacktestStatus };

// Whether a backtest can go in the comparison, and what its button says.
function useCompareState({ id, status }: Target) {
  const { picks, full } = useCompareSelection();
  const added = picks.some((pick) => pick.id === id);
  let why: string | undefined;
  if (status === 'running') why = 'Still running';
  else if (status === 'failed') why = 'Failed: no results to compare';
  else if (full && !added) why = 'Up to 4 backtests. Remove one first';
  return { added, why, title: why ?? (added ? 'Remove from compare' : 'Add to compare') };
}

function toggle(target: Target, added: boolean) {
  if (added) removeFromCompare(target.id);
  else addToCompare({ id: target.id, number: target.number, symbol: target.symbol });
}

// The scale icon of a row in a list of backtests: muted, the accent on hover, filled once added,
// faint with the reason in its title when it cannot be added. Above the row's own click target.
export function CompareToggle({ target }: { target: Target }) {
  const { added, why, title } = useCompareState(target);
  return (
    <button
      type="button"
      title={title}
      aria-label={`${title}: backtest ${target.number}`}
      aria-pressed={added}
      disabled={why !== undefined}
      onClick={() => {
        toggle(target, added);
      }}
      className={cn(
        'relative z-10 inline-grid size-8 flex-none cursor-pointer place-items-center rounded-lg border border-transparent text-muted-foreground transition-[color,background-color,border-color,transform] duration-100 outline-none focus-visible:ring-2 focus-visible:ring-ring/50 active:scale-[0.92] disabled:cursor-not-allowed disabled:opacity-30 disabled:active:scale-100 motion-reduce:transition-none',
        added
          ? 'border-primary bg-primary text-primary-foreground hover:bg-primary/85'
          : 'enabled:hover:border-primary/45 enabled:hover:bg-accent-soft enabled:hover:text-primary',
      )}
    >
      <Scale aria-hidden="true" className="size-4" />
    </button>
  );
}

// The button on a backtest's own page, with its name in words.
export function CompareButton({ target }: { target: Target }) {
  const { added, why, title } = useCompareState(target);
  return (
    <Button
      type="button"
      variant={added ? 'default' : 'outline'}
      title={title}
      aria-pressed={added}
      disabled={why !== undefined}
      className={BUTTON_SIZES.sm}
      onClick={() => {
        toggle(target, added);
      }}
    >
      <Scale aria-hidden="true" />
      {added ? 'Added to compare' : 'Add to compare'}
    </Button>
  );
}
