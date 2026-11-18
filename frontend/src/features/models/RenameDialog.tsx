import { useEffect, useRef, useState, type FormEvent } from 'react';

import { ApiError, errorMessage, isGone, type Model } from '@/api';
import { CancelButton, PrimaryButton } from '@/components/DialogButtons';
import { Hint } from '@/components/Hint';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NAME_REFUSED } from '@/lib/messages';
import { cn } from '@/lib/utils';

import { splitExtension } from './format';
import { nameOf, nameProblem } from './names';
import { useRenameModel } from './useModels';

function errorText(error: Error): string {
  if (error instanceof ApiError && error.problem === 'model-name-taken')
    return 'You already have a model with this name.';
  return errorMessage(error, NAME_REFUSED);
}

type FieldProps = { value: string; extension: string; onChange: (value: string) => void };

// The name without its extension (.md-in), joined to the extension it keeps (.md-ext); focused
// and selected on open, to type the new name over the old one.
function NameField({ value, extension, onChange }: FieldProps) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  return (
    <label className="flex flex-col gap-1.5">
      <Hint>Name</Hint>
      <span className="flex items-center rounded-md border bg-background focus-within:border-[color-mix(in_srgb,var(--accent)_55%,var(--rule))]">
        <input
          ref={input}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-label="Model name"
          className="h-[34px] w-full min-w-0 bg-transparent px-2.5 text-sm outline-none"
        />
        <span className="grid place-items-center self-stretch rounded-r-[5px] border-l bg-muted px-3 font-mono text-sm/[normal] text-muted-foreground">
          {extension}
        </span>
      </span>
    </label>
  );
}

type FormProps = { model: Model; onDone: () => void };

// Save is off while the name is empty, unchanged, one the API refuses or being saved; Enter
// saves. A name the API refuses, or one the user already has, turns the help line red.
function RenameForm({ model, onDone }: FormProps) {
  const [stem, extension] = splitExtension(model.name);
  const [text, setText] = useState(stem);
  const rename = useRenameModel();
  const name = nameOf(text);
  const problem = nameProblem(name) ?? (rename.error ? errorText(rename.error) : undefined);
  const canSave = name !== '' && name !== stem && nameProblem(name) === undefined && !rename.isPending;

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    // A model deleted meanwhile has nothing left to rename: the dialog closes and the tile goes.
    rename.mutate({ id: model.id, name }, { onSuccess: onDone, onError: (error) => isGone(error) && onDone() });
  };

  return (
    <form onSubmit={save} className="contents">
      <DialogBody>
        <NameField
          value={text}
          extension={extension}
          onChange={(value) => {
            setText(value);
            rename.reset();
          }}
        />
        <DialogDescription role={problem ? 'alert' : undefined} className={cn(problem && 'text-down')}>
          {problem ?? `The extension stays ${extension}. Backtests that used this model show the new name.`}
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

type Props = { model: Model | null; open: boolean; onOpenChange: (open: boolean) => void };

// Rename model (mockups/app, MODALS.rename). The model stays while the dialog fades out.
export function RenameDialog({ model, open, onOpenChange }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename model</DialogTitle>
        </DialogHeader>
        {model && <RenameForm key={model.id} model={model} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
