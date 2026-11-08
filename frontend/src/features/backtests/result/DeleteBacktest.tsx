import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Trash2 } from 'lucide-react';
import { useState } from 'react';

import { errorMessage, type BacktestDocument } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { DangerButton, OutlineButton } from '@/components/DialogButtons';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

import { forgetBacktest, useDeleteBacktest } from '../useBacktests';

const SMALL = cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'font-medium');

type Backtest = BacktestDocument['backtest'];

// What goes with it, by its status.
const GOES: Record<Backtest['status'], string> = {
  finished: 'It goes from your Backtests with its results and trades.',
  failed: 'It goes from your Backtests.',
  queued: 'It leaves your queue and goes from your Backtests.',
  running: 'It stops running and goes from your Backtests.',
};

function deleteText(backtest: Backtest): string {
  const published = backtest.public ? ' It also leaves Experiments and the Compare of anyone who added it.' : '';
  return `${GOES[backtest.status]}${published} This cannot be undone.`;
}

type DialogProps = {
  backtest: Backtest;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // What follows once the backtest is gone: its page goes to Backtests.
  onDeleted: () => void;
};

// Delete Bright Vega? (the dialogs' 560 px, as a model's MODALS.del): what goes with it and that it
// cannot be undone. A refusal stays in the dialog. It cannot be closed while the request runs, so
// its answer still leaves the page.
function DeleteDialog({ backtest, open, onOpenChange, onDeleted }: DialogProps) {
  const remove = useDeleteBacktest();
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
          <DialogTitle className="min-w-0 truncate">Delete {backtest.name}?</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <DialogDescription className="text-sm/[1.5] text-foreground">{deleteText(backtest)}</DialogDescription>
          {remove.isError && (
            <p role="alert" className="text-[12.5px] text-down">
              {errorMessage(remove.error, 'Could not delete the backtest. Try again.')}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <DialogClose asChild>
            <OutlineButton disabled={remove.isPending}>Cancel</OutlineButton>
          </DialogClose>
          <DangerButton
            disabled={remove.isPending}
            onClick={() => remove.mutate(backtest.id, { onSuccess: onDeleted })}
          >
            Delete
          </DangerButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Delete on the user's own backtest, whatever its status, an outline button with a bin that turns
// red on hover and asks first (DELETE /api/v1/backtests/{id}). Then the page goes to Backtests and
// forgets the document.
export function DeleteBacktest({ backtest }: { backtest: Backtest }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return (
    <>
      <Button type="button" variant="outline" className={cn(SMALL, 'hover:text-down')} onClick={() => setOpen(true)}>
        <Trash2 aria-hidden="true" />
        Delete
      </Button>
      <DeleteDialog
        backtest={backtest}
        open={open}
        onOpenChange={setOpen}
        onDeleted={() => {
          void navigate({ to: '/backtests' }).then(() => forgetBacktest(queryClient, backtest.id));
        }}
      />
    </>
  );
}
