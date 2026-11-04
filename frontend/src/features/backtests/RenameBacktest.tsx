import { Pencil } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { ApiError, errorMessage, type BacktestDocument } from '@/api';
import { BUTTON_SIZES } from '@/components/buttonStyles';
import { CancelButton, PrimaryButton } from '@/components/DialogButtons';
import { useToast } from '@/components/Toast';
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

type Named = Pick<BacktestDocument['backtest'], 'id' | 'name' | 'generated_name' | 'public'>;

// A name another backtest of the account has (409 name-taken): the API's sentence names it, and
// Save stays off until the text changes.
const isTaken = (error: Error | null) => error instanceof ApiError && error.status === 409;

// What the field has to say: why the name cannot be saved, in red with a dot (.rn-err), then the
// generated name with the way back to it, or that it is the generated one (.rn-gen).
function Hints({
  problem,
  generated,
  onGenerated,
}: {
  problem: string | null;
  generated: string | null;
  onGenerated: () => void;
}) {
  return (
    <div id="rename-hints" className="flex flex-col gap-1.5">
      {problem && (
        <p role="alert" className="flex items-start gap-[7px] text-[12.5px] text-down">
          <span aria-hidden="true" className="mt-[7px] size-1.5 flex-none rounded-full bg-down" />
          {problem}
        </p>
      )}
      <p className="flex min-h-5 flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted-foreground">
        {generated === null ? (
          'This is the name CandleStack gave it. Type your own, you can come back to this one later.'
        ) : (
          <>
            <span>
              Generated name <b className="font-medium text-foreground">{generated}</b>
            </span>
            <button
              type="button"
              onClick={onGenerated}
              className="cursor-pointer font-medium text-primary underline-offset-2 outline-none hover:underline focus-visible:underline"
            >
              Use the generated name
            </button>
          </>
        )}
      </p>
    </div>
  );
}

// Why the name cannot be saved: nothing typed, or what the API said (a 404 closes the page's
// dialog with the page).
function problemOf(next: string, error: Error | null): string | null {
  if (next === '') return 'A name can’t be empty.';
  if (!error || (error instanceof ApiError && error.status === 404)) return null;
  return errorMessage(error, NAME_REFUSED);
}

// The text being typed and what Save does with it. Saving the generated name takes the user's own
// away (`name: null`); a toast then says so.
function useRenameForm(backtest: Named, onDone: (note: string) => void) {
  const [text, setText] = useState(backtest.name);
  const rename = useRenameBacktest();
  const next = text.trim();
  const generated = next === backtest.generated_name;
  const canSave = next !== '' && next !== backtest.name && !isTaken(rename.error) && !rename.isPending;
  const edit = (value: string) => {
    setText(value);
    rename.reset();
  };
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    rename.mutate(
      { id: backtest.id, name: generated ? null : next },
      {
        onSuccess: (changed) => {
          onDone(generated ? `Back to the generated name, ${changed.name}` : `Renamed to ${changed.name}`);
        },
      },
    );
  };
  return { text, next, generated, problem: problemOf(next, rename.error), canSave, edit, save };
}

type FieldProps = { backtest: Named; form: ReturnType<typeof useRenameForm> };

// Name, and how many characters of 80 the trimmed name has (.fld-l).
function Label({ length }: { length: number }) {
  return (
    <div className="flex items-baseline justify-between text-[13px] font-medium">
      <label htmlFor="rename-name">Name</label>
      <span className="font-mono text-[11.5px] font-normal text-muted-foreground">
        {length} / {NAME_MAX}
      </span>
    </div>
  );
}

// Name with how many characters of 80 it has, the field, red while the name cannot be saved, and
// what it has to say under it (.fld).
function NameField({ backtest, form }: FieldProps) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  return (
    <div className="flex flex-col gap-1.5">
      <Label length={form.next.length} />
      <input
        id="rename-name"
        ref={input}
        value={form.text}
        maxLength={NAME_MAX}
        autoComplete="off"
        spellCheck={false}
        aria-describedby="rename-hints"
        aria-invalid={form.problem !== null}
        onChange={(event) => {
          form.edit(event.target.value);
        }}
        className={cn(
          'h-9 w-full min-w-0 rounded-md border bg-background px-2.5 text-sm outline-none focus:border-[color-mix(in_srgb,var(--accent)_55%,var(--rule))]',
          form.problem && 'border-[color-mix(in_srgb,var(--down)_60%,var(--rule))]',
        )}
      />
      <Hints
        problem={form.problem}
        generated={form.generated ? null : backtest.generated_name}
        onGenerated={() => {
          form.edit(backtest.generated_name);
          input.current?.focus();
        }}
      />
    </div>
  );
}

function RenameForm({ backtest, onDone }: { backtest: Named; onDone: (note: string) => void }) {
  const form = useRenameForm(backtest, onDone);
  return (
    <form onSubmit={form.save} className="contents">
      <DialogBody>
        <NameField backtest={backtest} form={form} />
        <DialogDescription className="text-[13px]">
          {backtest.public
            ? 'It’s public. The new name shows in Experiments and in other people’s Compare right away.'
            : 'Only you see it until you publish the backtest.'}
        </DialogDescription>
      </DialogBody>
      <DialogFooter>
        <CancelButton />
        <PrimaryButton type="submit" disabled={!form.canSave}>
          Save
        </PrimaryButton>
      </DialogFooter>
    </form>
  );
}

// Rename on a backtest's page (backtest-names, MODALS.rename): 1 to 80 characters, trimmed, unique
// in the account; the generated name stays one click away. A toast says what it is called now.
export function RenameBacktest({ backtest }: { backtest: Named }) {
  const [open, setOpen] = useState(false);
  const { toast, show } = useToast();
  return (
    <>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
          <Button type="button" variant="outline" className={BUTTON_SIZES.sm}>
            <Pencil aria-hidden="true" />
            Rename
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="min-w-0 truncate">Rename {backtest.name}</DialogTitle>
          </DialogHeader>
          {open && (
            <RenameForm
              backtest={backtest}
              onDone={(note) => {
                setOpen(false);
                show(note);
              }}
            />
          )}
        </DialogContent>
      </Dialog>
      {toast}
    </>
  );
}
