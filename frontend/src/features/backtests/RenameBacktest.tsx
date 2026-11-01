import { Pencil } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { ApiError, errorMessage } from '@/api';
import { BUTTON_SIZES } from '@/components/buttonStyles';
import { CancelButton, PrimaryButton } from '@/components/DialogButtons';
import { Hint } from '@/components/Hint';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { NAME_REFUSED } from '@/lib/messages';
import { cn } from '@/lib/utils';

import { useRenameBacktest } from './useBacktests';

const NAME_MAX = 80;

type Props = { id: string; name: string | null };

function RenameForm({ id, name, onDone }: Props & { onDone: () => void }) {
  const [text, setText] = useState(name ?? '');
  const input = useRef<HTMLInputElement>(null);
  const rename = useRenameBacktest();
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  const next = text.trim();
  const canSave = next !== (name ?? '') && !rename.isPending;
  const problem = rename.error && !(rename.error instanceof ApiError && rename.error.status === 404);

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    rename.mutate({ id, name: next === '' ? null : next }, { onSuccess: onDone });
  };

  return (
    <form onSubmit={save} className="contents">
      <DialogBody>
        <label className="flex flex-col gap-1.5">
          <Hint>Name</Hint>
          <input
            ref={input}
            value={text}
            maxLength={NAME_MAX}
            aria-label="Backtest name"
            onChange={(event) => {
              setText(event.target.value);
              rename.reset();
            }}
            className="h-[34px] w-full min-w-0 rounded-md border bg-background px-2.5 text-sm outline-none focus:border-[color-mix(in_srgb,var(--accent)_55%,var(--rule))]"
          />
        </label>
        <DialogDescription role={problem ? 'alert' : undefined} className={cn(problem && 'text-down')}>
          {problem && rename.error
            ? errorMessage(rename.error, NAME_REFUSED)
            : 'Leave it empty to go back to the number.'}
        </DialogDescription>
      </DialogBody>
      <DialogFooter>
        <CancelButton />
        <PrimaryButton type="submit" disabled={!canSave}>
          Save
        </PrimaryButton>
      </DialogFooter>
    </form>
  );
}

// Rename on a backtest's page: the same dialog as a model's, without an extension to keep.
export function RenameBacktest({ id, name }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" className={BUTTON_SIZES.sm}>
          <Pencil aria-hidden="true" />
          Rename
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename backtest</DialogTitle>
        </DialogHeader>
        {open && (
          <RenameForm
            id={id}
            name={name}
            onDone={() => {
              setOpen(false);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
