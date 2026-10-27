import { File as FileIcon } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import type { Model } from '@/api';
import { UploadIcon } from '@/components/icons';
import { DIALOG_HINT } from '@/components/ui/dialog';

import { DropZone } from './DropZone';
import { formatLimit, formatSize } from './format';
import { API_FRAMEWORKS, frameworkOfFile, UPLOAD_FRAMEWORKS } from './frameworks';
import { InputMatrix } from './InputMatrix';
import { formatDims, formatGrid, formatReads, formatWarmUp, representationName } from './manifest';
import { manifestProblem, modelProblem, type Problem } from './problems';
import { Facts, LinkButton, MatchBar, OutLink, Step, StepError, StepLine } from './UploadSteps';
import { useModels } from './useModels';
import { heldManifest, type ManifestStep, type ModelStep } from './useUpload';

// The three steps of the upload (mockups/app, dModalBody), each in the state it is in.

const MANIFEST_DOCS = 'https://candlestack.tech/docs/manifest';
const ONNX_EXPORT = 'https://onnx.ai/onnx/intro/converters.html';

const mono = (text: string) => <span className="font-mono">{text}</span>;
const shapeOf = (shape: number[] | null) => mono(shape && shape.length > 0 ? formatDims(shape) : '–');

const Line = ({ children }: { children: ReactNode }) => <p className={DIALOG_HINT}>{children}</p>;
const Muted = ({ children }: { children: ReactNode }) => <span className="text-muted-foreground">{children}</span>;

type Picker = { onPick: () => void; onDrop: (file: File) => void };

// A step that failed, its problem in its place.
function FailedStep({ n, title, problem, onPick }: { n: number; title: string; problem: Problem; onPick: () => void }) {
  return (
    <Step n={n} state="bad" title={title}>
      <StepError title={problem.title} lines={problem.lines} onAgain={onPick} />
    </Step>
  );
}

// A file being checked: its name and what else is known of it.
function BusyStep({ n, title, file, children }: { n: number; title: string; file: File; children: ReactNode }) {
  return (
    <Step n={n} state="busy" title={title}>
      <StepLine name={file.name}>
        <Muted>{children}</Muted>
      </StepLine>
    </Step>
  );
}

// "up to 50 MB" once the API said its limit (`file_max_bytes`).
const upTo = (fileMax: number | undefined) => (fileMax === undefined ? '' : `, up to ${formatLimit(fileMax)}`);

function PickModel({ onPick, onDrop }: Picker) {
  const fileMax = useModels().data?.usage.file_max_bytes;
  return (
    <Step n={1} state="on" title="Upload the model">
      <Line>A trained model in Keras (.keras) or ONNX (.onnx){upTo(fileMax)}.</Line>
      <DropZone
        icon={UploadIcon}
        title="Drop the model here"
        frameworks={UPLOAD_FRAMEWORKS}
        onPick={onPick}
        onDrop={onDrop}
      />
      <Line>
        PyTorch, TensorFlow or scikit-learn? Export the model to ONNX first.{' '}
        <OutLink href={ONNX_EXPORT}>How to export</OutLink>
      </Line>
    </Step>
  );
}

// The model once kept: its name and what it takes and gives, its size when it was just uploaded.
function KeptModel({ model, adding }: { model: Model; adding: boolean }) {
  const facts: [string, ReactNode][] = [
    ['Format', API_FRAMEWORKS[model.framework]],
    ['Input', shapeOf(model.input_shape)],
    ['Output', shapeOf(model.output_shape)],
  ];
  if (!adding) facts.push(['Size', formatSize(model.size_bytes)]);
  return (
    <Step n={1} state="done" title={adding ? 'The model' : 'Model uploaded'}>
      <StepLine name={model.name} />
      <Facts facts={facts} />
    </Step>
  );
}

type ModelProps = Picker & { state: ModelStep; adding: boolean };

// Step 1: the model file (Keras or ONNX). Adding a manifest to a model, the step is the model.
export function ModelPart({ state, adding, onPick, onDrop }: ModelProps) {
  const title = 'Upload the model';
  switch (state.step) {
    case 'pick':
      return <PickModel onPick={onPick} onDrop={onDrop} />;
    case 'checking':
      return (
        <BusyStep n={1} title={title} file={state.file}>
          {frameworkOfFile(state.file.name)} · {formatSize(state.file.size)}
        </BusyStep>
      );
    case 'failed':
      return <FailedStep n={1} title={title} problem={modelProblem(state.failure)} onPick={onPick} />;
    case 'kept':
      return <KeptModel model={state.model} adding={adding} />;
  }
}

function PickManifest({ onPick, onDrop }: Picker) {
  return (
    <Step n={2} state="on" title="Upload the manifest">
      <Line>
        The manifest tells CandleStack how to build the model&apos;s input: which candles, which columns in which order,
        how many rows. <OutLink href={MANIFEST_DOCS}>About the manifest format</OutLink>
      </Line>
      <DropZone icon={FileIcon} title="Drop manifest.json here" onPick={onPick} onDrop={onDrop} />
    </Step>
  );
}

type KeptProps = { state: Extract<ManifestStep, { step: 'kept' }>; model: Model; onPick: () => void };

// The manifest once kept: what it reads, the bar where the model's input and the manifest's
// meet, and under it, on demand, the input drawn as a matrix.
function KeptManifest({ state, model, onPick }: KeptProps) {
  const [open, setOpen] = useState(false);
  const summary = model.manifest_summary;
  const shape = formatDims(state.manifest.signature.input.shape);
  return (
    <Step n={2} state="done" title="Manifest uploaded">
      <StepLine name={state.file.name}>
        <LinkButton onClick={onPick}>Change</LinkButton>
      </StepLine>
      {summary && (
        <Facts
          facts={[
            ['Input', mono(formatDims(summary.input_shape))],
            ['Output', mono(formatDims(state.manifest.signature.output.shape))],
            ['Reads', formatReads(summary)],
            ['Warm-up', formatWarmUp(summary)],
          ]}
        />
      )}
      <MatchBar
        model={model.input_shape ? formatDims(model.input_shape) : shape}
        manifest={shape}
        expanded={open}
        onToggle={() => setOpen(!open)}
      />
      {open && <InputMatrix manifest={state.manifest} />}
    </Step>
  );
}

// Why a manifest failed, and that the model keeps the earlier one it had (after Change).
function failedManifest(state: Extract<ManifestStep, { step: 'failed' }>): Problem {
  const problem = manifestProblem(state.failure);
  if (!state.earlier) return problem;
  return { ...problem, lines: [...problem.lines, `The model keeps ${state.earlier.file.name}.`] };
}

type ManifestProps = Picker & { state: ManifestStep; model: Model | null };

// Step 2: the manifest, which waits for the model. A model needs one to be added.
export function ManifestPart({ state, model, onPick, onDrop }: ManifestProps) {
  const title = 'Upload the manifest';
  if (model === null) return <Step n={2} state="wait" title={title} />;
  switch (state.step) {
    case 'pick':
      return <PickManifest onPick={onPick} onDrop={onDrop} />;
    case 'checking':
      return (
        <BusyStep n={2} title={title} file={state.file}>
          Checking
        </BusyStep>
      );
    case 'failed':
      return <FailedStep n={2} title={title} problem={failedManifest(state)} onPick={onPick} />;
    case 'kept':
      return <KeptManifest state={state} model={model} onPick={onPick} />;
  }
}

type SummaryProps = { model: Model | null; manifest: ManifestStep; adding: boolean };

// Step 3: one line of what goes to Models, once the API holds a manifest for the model (the model
// it answered with has its summary).
export function SummaryPart({ model, manifest, adding }: SummaryProps) {
  const summary = model?.manifest_summary;
  if (!model || heldManifest(manifest) === undefined || !summary) {
    return <Step n={3} state="wait" title={adding ? 'Save' : 'Add the model'} />;
  }
  return (
    <Step n={3} state="on" title={adding ? 'Save the manifest' : 'Add the model'}>
      <StepLine name={model.name}>
        <Muted>
          {representationName(summary.representation.id, true)}, {summary.timeframe} · {formatGrid(summary.input_shape)}
        </Muted>
      </StepLine>
    </Step>
  );
}
