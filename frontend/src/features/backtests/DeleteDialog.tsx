import { Trash2 } from 'lucide-react';
import type { ReactNode } from 'react';

import { errorMessage, type Backtest } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { CancelButton, DangerButton } from '@/components/DialogButtons';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { plural } from '@/lib/plural';
import { cn } from '@/lib/utils';

import { useDeleteBacktests } from './useBacktests';

// The button that opens it, on a backtest's page and over the list: outline with a bin, red on
// hover.
export function DeleteButton({
  className,
  onClick,
  children,
}: {
  className?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      variant="outline"
      className={cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'font-medium hover:text-down', className)}
      onClick={onClick}
    >
      <Trash2 aria-hidden="true" />
      {children}
    </Button>
  );
}

// What the dialog needs of each backtest; a list's row and a page's document both have it.
type Doomed = Pick<Backtest, 'id' | 'name' | 'status' | 'public'>;

// What goes with one backtest, by its status.
const GOES: Record<Doomed['status'], string> = {
  finished: 'It goes from your Backtests with its results and trades.',
  failed: 'It goes from your Backtests.',
  queued: 'It leaves your queue and goes from your Backtests.',
  running: 'It stops running and goes from your Backtests.',
};

function oneText(backtest: Doomed): string {
  const published = backtest.public ? ' It also leaves Experiments and the Compare of anyone who added it.' : '';
  return `${GOES[backtest.status]}${published}`;
}

// Several: what happens to the running, queued and published ones among them, when there are any.
function manyText(backtests: readonly Doomed[]): string {
  const any = (test: (backtest: Doomed) => boolean) => backtests.some(test);
  return [
    'They go from your Backtests with their results and trades.',
    any((backtest) => backtest.status === 'running') && 'Running ones are stopped.',
    any((backtest) => backtest.status === 'queued') && 'Queued ones leave your queue.',
    any((backtest) => backtest.public) &&
      'Published ones also leave Experiments and the Compare of anyone who added them.',
  ]
    .filter(Boolean)
    .join(' ');
}

function deleteText(backtests: readonly Doomed[]): string {
  const [only] = backtests;
  const text = only && backtests.length === 1 ? oneText(only) : manyText(backtests);
  return `${text} This cannot be undone.`;
}

const titleOf = (backtests: readonly Doomed[]) =>
  backtests.length === 1 && backtests[0]
    ? `Delete ${backtests[0].name}?`
    : `Delete ${plural(backtests.length, 'backtest')}?`;

const ids = (backtests: readonly Doomed[]) => backtests.map((backtest) => backtest.id);

type Props = {
  backtests: readonly Doomed[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // What follows once they are gone, with the IDs that are: the page goes to Backtests, the list
  // clears its selection.
  onDeleted: (gone: readonly string[]) => void;
};

// Delete Bright Vega? or Delete 3 backtests? (the dialogs' 560 px, as a model's MODALS.del): what
// goes with them and that it cannot be undone. A refusal stays in the dialog. It cannot be closed
// while the request runs, so its answer still does what follows.
export function DeleteDialog({ backtests, open, onOpenChange, onDeleted }: Props) {
  const remove = useDeleteBacktests();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (remove.isPending) return;
        remove.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="min-w-0 truncate">{titleOf(backtests)}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <DialogDescription className="text-sm/[1.5] text-foreground">{deleteText(backtests)}</DialogDescription>
          {remove.isError && (
            <p role="alert" className="text-[12.5px] text-down">
              {errorMessage(remove.error, 'Could not delete. Try again.')}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <CancelButton />
          <DangerButton
            disabled={remove.isPending}
            onClick={() => remove.mutate(ids(backtests), { onSuccess: onDeleted })}
          >
            Delete
          </DangerButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
