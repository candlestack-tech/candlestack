import { useNavigate } from '@tanstack/react-router';
import { useRef, useState, type ReactNode, type RefObject } from 'react';

import type { Model } from '@/api';
import { CancelButton, PrimaryButton } from '@/components/DialogButtons';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';

import { ACCEPTED_EXTENSIONS } from './frameworks';
import { ManifestPart, ModelPart, SummaryPart } from './UploadParts';
import { Steps } from './UploadSteps';
import { useUpload, type ManifestStep, type ModelStep } from './useUpload';

// A hidden file input that the drop zones and Choose another file open.
function FileInput({
  input,
  label,
  accept,
  onFile,
}: {
  input: RefObject<HTMLInputElement | null>;
  label: string;
  accept: string;
  onFile: (file: File) => void;
}) {
  return (
    <input
      ref={input}
      type="file"
      accept={accept}
      hidden
      aria-label={label}
      onChange={(event) => {
        const file = event.target.files?.[0];
        // Cleared, so choosing the same file again is a change too.
        event.target.value = '';
        if (file) onFile(file);
      }}
    />
  );
}

// What screen readers hear as the steps go on; a failure is an alert of its own.
function status(model: ModelStep, manifest: ManifestStep): string {
  if (manifest.step === 'kept') return 'Manifest uploaded. It fits the model.';
  if (manifest.step === 'checking') return 'Checking the manifest';
  if (model.step === 'kept') return `${model.model.name} uploaded`;
  if (model.step === 'checking') return 'Checking the model';
  return '';
}

type FlowProps = {
  // Adding a manifest to this model; without one, a new model.
  existing?: Model;
  onAdded: (model: Model) => void;
};

// The hidden file inputs and how the drop zones and Choose another file open them: the step
// starts over, then the picker opens.
function usePickers(flow: ReturnType<typeof useUpload>) {
  const modelInput = useRef<HTMLInputElement>(null);
  const manifestInput = useRef<HTMLInputElement>(null);
  const chooseManifest = (file: File) => void flow.chooseManifest(file);
  const inputs = (
    <>
      <FileInput input={modelInput} label="Model file" accept={ACCEPTED_EXTENSIONS} onFile={flow.chooseModel} />
      <FileInput input={manifestInput} label="Manifest file" accept=".json,application/json" onFile={chooseManifest} />
    </>
  );
  const pickModel = () => {
    flow.resetModel();
    modelInput.current?.click();
  };
  const pickManifest = () => {
    flow.resetManifest();
    manifestInput.current?.click();
  };
  return { inputs, pickModel, pickManifest, chooseManifest };
}

// The three steps, all in view, and Cancel and Add model (Save) under them. It mounts with the
// dialog and unmounts with it; a file still being checked goes on to its answer, and a new model
// not added goes when it closes (useUpload).
function UploadFlow({ existing, onAdded }: FlowProps) {
  const flow = useUpload(existing);
  const pickers = usePickers(flow);
  const adding = existing !== undefined;
  const model = flow.model.step === 'kept' ? flow.model.model : null;
  const ready = model !== null && flow.manifest.step === 'kept';
  return (
    <>
      <DialogBody>
        {pickers.inputs}
        <p aria-live="polite" className="sr-only">
          {status(flow.model, flow.manifest)}
        </p>
        <Steps>
          <ModelPart state={flow.model} adding={adding} onPick={pickers.pickModel} onDrop={flow.chooseModel} />
          <ManifestPart
            state={flow.manifest}
            model={model}
            onPick={pickers.pickManifest}
            onDrop={pickers.chooseManifest}
          />
          <SummaryPart model={model} manifest={flow.manifest} adding={adding} />
        </Steps>
      </DialogBody>
      <DialogFooter>
        <CancelButton />
        <PrimaryButton
          disabled={!ready}
          onClick={() => {
            if (!model) return;
            flow.keep();
            onAdded(model);
          }}
        >
          {adding ? 'Save' : 'Add model'}
        </PrimaryButton>
      </DialogFooter>
    </>
  );
}

// Add a model (mockups/app, MODALS.upload): the model file, its manifest, then Add model, which
// opens the model's page, or with `onAdded` hands the model over (the builder chooses it). A model
// needs its manifest to be added: the API keeps the model as soon as its file passed the checks,
// so closing the dialog before Add model deletes the model it uploaded.
export function UploadDialog({ children, onAdded }: { children: ReactNode; onAdded?: (model: Model) => void }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Add a model</DialogTitle>
        </DialogHeader>
        <UploadFlow
          onAdded={(model) => {
            setOpen(false);
            if (onAdded) onAdded(model);
            else void navigate({ to: '/models/$modelId', params: { modelId: model.id } });
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

type ManifestDialogProps = {
  model: Model | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // After Save, with the model (the builder chooses it).
  onSaved?: (model: Model) => void;
};

// Add a manifest to {name} (mockups/app, MODALS.man): the same steps with the model done. The
// model stays while the dialog fades out.
export function ManifestDialog({ model, open, onOpenChange, onSaved }: ManifestDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle className="min-w-0 break-words">
            {model ? `Add a manifest to ${model.name}` : 'Add a manifest'}
          </DialogTitle>
        </DialogHeader>
        {model && (
          <UploadFlow
            key={model.id}
            existing={model}
            onAdded={(saved) => {
              onOpenChange(false);
              onSaved?.(saved);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
