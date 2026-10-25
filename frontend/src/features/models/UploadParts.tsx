import { File as FileIcon } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import type { Model } from '@/api';
import { UploadIcon } from '@/components/icons';
import { DIALOG_HINT } from '@/components/ui/dialog';

import { DropZone } from './DropZone';
import { formatSize } from './format';
import { API_FRAMEWORKS, frameworkOfFile, UPLOAD_FRAMEWORKS } from './frameworks';
import { InputMatrix } from './InputMatrix';
import { formatDims, formatGrid, formatReads, formatWarmUp, representationName } from './manifest';
import { manifestProblem, modelProblem, type Problem } from './problems';
import { Facts, LinkButton, MatchBar, OutLink, Step, StepError, StepLine } from './UploadSteps';
import type { ManifestStep, ModelStep } from './useUpload';

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

function PickModel({ onPick, onDrop }: Picker) {
  return (
    <Step n={1} state="on" title="Upload the model">
      <Line>A trained model in Keras (.keras) or ONNX (.onnx), up to 50 MB.</Line>
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

function PickManifest({ adding, onPick, onDrop, onSkip }: Picker & { adding: boolean; onSkip: () => void }) {
  return (
    <Step n={2} state="on" title="Upload the manifest">
      <Line>
        The manifest tells CandleStack how to build the model&apos;s input: which candles, which columns in which order,
        how many rows. <OutLink href={MANIFEST_DOCS}>About the manifest format</OutLink>
      </Line>
      <DropZone icon={FileIcon} title="Drop manifest.json here" onPick={onPick} onDrop={onDrop} />
      {!adding && (
        <p>
          <LinkButton onClick={onSkip}>Add the model without a manifest</LinkButton>
        </p>
      )}
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

type ManifestProps = Picker & { state: ManifestStep; model: Model | null; adding: boolean; onSkip: () => void };

// Step 2: the manifest, which waits for the model. A new model can go without one for now.
export function ManifestPart({ state, model, adding, onPick, onDrop, onSkip }: ManifestProps) {
  const title = 'Upload the manifest';
  if (model === null) return <Step n={2} state="wait" title={title} />;
  switch (state.step) {
    case 'pick':
      return <PickManifest adding={adding} onPick={onPick} onDrop={onDrop} onSkip={onSkip} />;
    case 'checking':
      return (
        <BusyStep n={2} title={title} file={state.file}>
          Checking
        </BusyStep>
      );
    case 'failed':
      return <FailedStep n={2} title={title} problem={manifestProblem(state.failure)} onPick={onPick} />;
    case 'kept':
      return <KeptManifest state={state} model={model} onPick={onPick} />;
    case 'skipped':
      return (
        <Step n={2} state="done" title="No manifest for now">
          <Line>
            You can add it later from Models. <LinkButton onClick={onPick}>Add it now</LinkButton>
          </Line>
        </Step>
      );
  }
}

type SummaryProps = { model: Model | null; manifest: ManifestStep; adding: boolean };

// Step 3: one line of what goes to Models.
export function SummaryPart({ model, manifest, adding }: SummaryProps) {
  const ready = model !== null && (manifest.step === 'kept' || manifest.step === 'skipped');
  if (!ready) return <Step n={3} state="wait" title={adding ? 'Save' : 'Add the model'} />;
  const summary = model.manifest_summary;
  return (
    <Step n={3} state="on" title={adding ? 'Save the manifest' : 'Add the model'}>
      {manifest.step === 'skipped' || !summary ? (
        <p className="[overflow-wrap:anywhere]">
          {model.name} goes to Models as <b className="font-semibold">Needs setup</b>. It can run once it has a
          manifest.
        </p>
      ) : (
        <StepLine name={model.name}>
          <Muted>
            {representationName(summary.representation.id, true)}, {summary.timeframe} ·{' '}
            {formatGrid(summary.input_shape)}
          </Muted>
        </StepLine>
      )}
    </Step>
  );
}
