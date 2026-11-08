import { X } from 'lucide-react';
import { useState } from 'react';

import { errorMessage, type BacktestDocument } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { DangerButton, OutlineButton } from '@/components/DialogButtons';
import { useToast } from '@/components/Toast';
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

import { useCancelBacktest } from '../useBacktests';

const SMALL = cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'font-medium');

type Backtest = BacktestDocument['backtest'];

type DialogProps = {
  backtest: Backtest;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
};

// Keep it and Cancel backtest: neither while the request runs, no Cancel once it left the queue.
function CancelFooter({ pending, left, onConfirm }: { pending: boolean; left: boolean; onConfirm: () => void }) {
  return (
    <DialogFooter>
      <DialogClose asChild>
        <OutlineButton disabled={pending}>Keep it</OutlineButton>
      </DialogClose>
      <DangerButton disabled={pending || left} onClick={onConfirm}>
        Cancel backtest
      </DangerButton>
    </DialogFooter>
  );
}

// Cancel Bright Vega? (the dialogs' 560 px): it leaves the queue and stays in Backtests as failed.
// Keep it closes the dialog, though not while the request runs, so its answer still closes it with
// the toast. Where it began to run meanwhile, the API's sentence says so; where the page's poll
// finds it running first, the dialog says it has left the queue and Cancel backtest turns off.
function CancelDialog({ backtest, open, onOpenChange, onDone }: DialogProps) {
  const cancel = useCancelBacktest(backtest.id);
  const left = backtest.status !== 'queued';
  const refusal = cancel.isError
    ? errorMessage(cancel.error, 'Could not cancel the backtest. Try again.')
    : left && cancel.isIdle && 'It has left the queue, so it can no longer be cancelled.';
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (cancel.isPending) return;
        cancel.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="min-w-0 truncate">Cancel {backtest.name}?</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <DialogDescription className="text-sm/[1.5] text-foreground">
            It leaves your queue and never runs. It stays in your Backtests as failed.
          </DialogDescription>
          {refusal && (
            <p role="alert" className="text-[12.5px] text-down">
              {refusal}
            </p>
          )}
        </DialogBody>
        <CancelFooter
          pending={cancel.isPending}
          left={left}
          onConfirm={() => cancel.mutate(undefined, { onSuccess: onDone })}
        />
      </DialogContent>
    </Dialog>
  );
}

// Cancel backtest on the user's own queued backtest, an outline button with a cross that asks
// first (POST /api/v1/backtests/{id}/cancel), then a toast. It stays mounted once the backtest
// has left the queue, so the toast and a refusal in the open dialog still show.
export function CancelQueued({ backtest }: { backtest: Backtest }) {
  const [open, setOpen] = useState(false);
  const { toast, show } = useToast();
  return (
    <>
      {backtest.status === 'queued' && (
        <Button type="button" variant="outline" className={SMALL} onClick={() => setOpen(true)}>
          <X aria-hidden="true" />
          Cancel backtest
        </Button>
      )}
      <CancelDialog
        backtest={backtest}
        open={open}
        onOpenChange={setOpen}
        onDone={() => {
          setOpen(false);
          show(`${backtest.name} was cancelled`);
        }}
      />
      {toast}
    </>
  );
}
