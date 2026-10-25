import { useState } from 'react';

import { errorMessage, type Model } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { ErrorNote } from '@/components/ErrorNote';
import { Hint } from '@/components/Hint';
import { UploadIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { FrameworkTile, frameworkOf, ManifestDialog, UploadDialog, useModels } from '@/features/models';
import { cn } from '@/lib/utils';

import { canRun } from './form';
import { preprocessingLine } from './format';

const SMALL_BUTTON = cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'font-medium');

type RowProps = { model: Model; chosen: boolean; onChoose: () => void; onAddManifest: () => void };

// The radio (.radio): a dot in the accent when chosen, dashed when the model cannot run.
function Radio({ chosen, runs }: { chosen: boolean; runs: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'grid size-4 flex-none place-items-center rounded-full border-[1.5px]',
        chosen && 'border-primary',
        !runs && 'border-dashed',
      )}
    >
      {chosen && <span className="size-2 rounded-full bg-primary" />}
    </span>
  );
}

// What can be clicked to choose it: the radio, the framework's mark, the name over its
// preprocessing in one line, from its manifest.
function Pick({ model, chosen, onChoose }: Omit<RowProps, 'onAddManifest'>) {
  const runs = canRun(model);
  const framework = frameworkOf(model.framework);
  const line = runs && model.manifest_summary ? preprocessingLine(model.manifest_summary) : 'Needs a manifest';
  return (
    <button
      type="button"
      aria-pressed={chosen}
      disabled={!runs}
      onClick={onChoose}
      className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 rounded-lg py-2.5 pr-2.5 pl-3 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-default"
    >
      <Radio chosen={chosen} runs={runs} />
      {framework && <FrameworkTile framework={framework} />}
      <span className="flex min-w-0 flex-col">
        <span className={cn('truncate font-medium', !runs && 'text-muted-foreground')}>{model.name}</span>
        <span className="truncate font-mono text-xs/[17px] text-muted-foreground">{line}</span>
      </span>
    </button>
  );
}

// A model to choose (.b-mrow). One without a manifest is shown dimmed, cannot be chosen and
// offers Add manifest.
function ModelRow({ model, chosen, onChoose, onAddManifest }: RowProps) {
  const runs = canRun(model);
  return (
    <li
      className={cn(
        'flex items-center gap-2.5 rounded-lg border bg-background pr-2.5',
        chosen && 'border-primary shadow-[inset_0_0_0_1px_var(--accent)]',
        !runs && 'bg-muted',
      )}
    >
      <Pick model={model} chosen={chosen} onChoose={onChoose} />
      {!runs && (
        <Button
          type="button"
          variant="outline"
          className={SMALL_BUTTON}
          aria-label={`Add manifest to ${model.name}`}
          onClick={onAddManifest}
        >
          Add manifest
        </Button>
      )}
    </li>
  );
}

// onChoose takes the model's ID: the list decides whether it can run (a new upload or a manifest
// just added shows there first).
type Props = { chosen: Model | undefined; onChoose: (modelId: string) => void };

// Step 1 (stepModel): every model of the user, then Upload model, which comes back with the new
// model chosen. Adding a manifest to one chooses it too.
export function ModelStep({ chosen, onChoose }: Props) {
  const { data, error } = useModels();
  const [adding, setAdding] = useState<Model | null>(null);
  const [manifestOpen, setManifestOpen] = useState(false);
  const models = data?.models;
  return (
    <>
      {error && !models && <ErrorNote>{errorMessage(error, 'Could not load your models.')}</ErrorNote>}
      {models?.length === 0 && <Hint>No models yet</Hint>}
      {models && models.length > 0 && (
        <ul aria-label="Models" className="flex flex-col gap-1.5">
          {models.map((model) => (
            <ModelRow
              key={model.id}
              model={model}
              chosen={model.id === chosen?.id}
              onChoose={() => onChoose(model.id)}
              onAddManifest={() => {
                setAdding(model);
                setManifestOpen(true);
              }}
            />
          ))}
        </ul>
      )}
      <UploadDialog onAdded={(model) => onChoose(model.id)}>
        <Button type="button" variant="outline" className={cn(SMALL_BUTTON, 'self-start')}>
          <UploadIcon />
          Upload model
        </Button>
      </UploadDialog>
      <ManifestDialog
        model={adding}
        open={manifestOpen}
        onOpenChange={setManifestOpen}
        onSaved={(model) => onChoose(model.id)}
      />
    </>
  );
}
