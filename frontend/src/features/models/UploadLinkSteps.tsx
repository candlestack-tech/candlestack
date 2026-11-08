import { Link } from '@tanstack/react-router';
import { Check, ExternalLink, Link2 } from 'lucide-react';
import { useRef, type RefObject } from 'react';

import type { Model, ModelUpload, StorageUsage } from '@/api';
import { ClockIcon, UploadIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';

import { CheckRow, type CheckItem } from './CheckRow';
import { DropZone } from './DropZone';
import { formatSize, formatUsage } from './format';
import { API_FRAMEWORKS } from './frameworks';
import { modelProblem } from './problems';
import { linkFramework, type ReadyNote } from './useLinkUpload';
import { ErrorBox, FileRow, MetaLine, Tag } from './UploadLinkParts';
import { PRIMARY, SECONDARY } from './uploadLinkStyles';
import type { UploadFailure } from './useUpload';

// What the card of the upload page shows inside (design/mockups/api-mcp, pageUpload): the file
// the link waits for and the drop zone, the checks, the new model, why the upload failed.

const MODELS_DOCS = 'https://candlestack.tech/docs/models';

// Open the model: its page.
export const OpenModel = ({ id }: { id: string }) => (
  <Button asChild className={PRIMARY}>
    <Link to="/models/$modelId" params={{ modelId: id }}>
      Open the model
    </Link>
  </Button>
);

// A size, or the bytes when two sizes read the same in MB.
const sizeOf = (bytes: number, other: number) =>
  formatSize(bytes) === formatSize(other) ? `${bytes.toLocaleString('en-US')} bytes` : formatSize(bytes);

// Uploaded, then the two checks of the file (opens as a safe model, has trained weights), as far
// as they got: `failed` names the check that failed with why.
function checksOf(state: 'running' | 'passed' | UploadFailure): CheckItem[] {
  const uploaded: CheckItem = { state: 'passed', title: 'Uploaded' };
  if (state === 'running' || state === 'passed') {
    return [
      uploaded,
      { state, title: 'Opens as a safe model' },
      { state: state === 'running' ? 'todo' : 'passed', title: 'Has trained weights' },
    ];
  }
  const detail = 'detail' in state ? state.detail : undefined;
  if (state.kind === 'weights') {
    return [
      uploaded,
      { state: 'passed', title: 'Opens as a safe model' },
      { state: 'failed', title: 'No trained weights in the file', hint: detail },
    ];
  }
  return [
    uploaded,
    { state: 'failed', title: 'Does not open as a safe model', hint: detail },
    { state: 'none', title: 'Has trained weights', hint: 'Not checked' },
  ];
}

// The checks as far as they got, as a list (.cks).
export const LinkChecks = ({ state }: { state: 'running' | 'passed' | UploadFailure }) => (
  <ul className="flex flex-col gap-2.5 text-[13.5px]">
    {checksOf(state).map((item) => (
      <CheckRow key={item.title} {...item} />
    ))}
  </ul>
);

export function WrongFileNote({ note }: { note: ReadyNote }) {
  if (note.kind === 'refused') return <ErrorBox title={note.title}>{note.detail}</ErrorBox>;
  const { file, expected } = note;
  const framework = linkFramework(expected);
  const what =
    note.reason === 'format'
      ? `${file.name} is not a ${framework ?? 'model'} file.`
      : `${file.name} is ${sizeOf(file.size, expected.size_bytes)}.`;
  return (
    <ErrorBox title="Not the file the link is for">
      {what} Choose {expected.name}, or ask your agent for a new link.
    </ErrorBox>
  );
}

type ReadyProps = { link: ModelUpload; usage: StorageUsage | undefined; note: ReadyNote | undefined; now: number };

// The link waits for its file: the file it expects, the drop zone, the time left and the storage
// the file takes.
// The hidden file input the drop zone opens, offering files of the link's format.
function FileInput({
  input,
  link,
  onFile,
}: {
  input: RefObject<HTMLInputElement | null>;
  link: ModelUpload;
  onFile: (file: File) => void;
}) {
  return (
    <input
      ref={input}
      type="file"
      accept={link.name.slice(link.name.lastIndexOf('.'))}
      hidden
      aria-label="Model file"
      onChange={(event) => {
        const file = event.target.files?.[0];
        // Cleared, so choosing the same file again is a change too.
        event.target.value = '';
        if (file) onFile(file);
      }}
    />
  );
}

export function Ready({ link, usage, note, now, onFile }: ReadyProps & { onFile: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const framework = linkFramework(link);
  const minutesLeft = Math.max(1, Math.ceil((Date.parse(link.expires_at) - now) / 60_000));
  const free = usage ? Math.max(0, usage.quota_bytes - usage.used_bytes) : undefined;
  return (
    <>
      <FileInput input={input} link={link} onFile={onFile} />
      <FileRow name={link.name} framework={framework} line={`${formatSize(link.size_bytes)} · ${framework ?? ''}`}>
        <Tag>Expected</Tag>
      </FileRow>
      {note && <WrongFileNote note={note} />}
      <DropZone
        icon={UploadIcon}
        title={`Drop ${link.name} here`}
        onPick={() => input.current?.click()}
        onDrop={onFile}
      />
      <MetaLine
        left={
          <>
            <ClockIcon />
            Link works for {minutesLeft} more min
          </>
        }
        right={free === undefined ? undefined : `${formatSize(link.size_bytes)} of your ${formatSize(free)} free`}
      />
    </>
  );
}

export function Done({
  model,
  usedBefore,
  usage,
}: {
  model: Model;
  usedBefore: number | undefined;
  usage: StorageUsage | undefined;
}) {
  const input = model.input_shape && model.input_shape.length > 0 ? ` · input ${model.input_shape.join(' × ')}` : '';
  const storage =
    usedBefore === undefined || !usage
      ? undefined
      : `${formatSize(usedBefore)} → ${formatUsage(usedBefore + model.size_bytes, usage.quota_bytes)}`;
  return (
    <>
      <FileRow
        name={model.name}
        framework={API_FRAMEWORKS[model.framework]}
        line={`${formatSize(model.size_bytes)} · ${API_FRAMEWORKS[model.framework]}${input}`}
      >
        <Tag>New</Tag>
      </FileRow>
      <LinkChecks state="passed" />
      <div className="flex gap-2.5 [&>*]:flex-1">
        <OpenModel id={model.id} />
      </div>
      <MetaLine
        left={
          <>
            <Check aria-hidden="true" />
            You can close this tab
          </>
        }
        right={storage}
      />
    </>
  );
}

// The upload failed: the checks as far as they got, or what else stopped it. The link is used up.
export function Failed({ link, failure }: { link: ModelUpload; failure: UploadFailure }) {
  const checkFailed = failure.kind === 'opens' || failure.kind === 'weights';
  const problem = modelProblem(failure);
  const framework = linkFramework(link);
  return (
    <>
      <FileRow
        name={link.name}
        framework={framework}
        line={`${formatSize(link.size_bytes)} · ${framework ?? ''}`}
        failed
      />
      {checkFailed ? (
        <LinkChecks state={failure} />
      ) : (
        <ErrorBox title={problem.title}>{problem.lines.join(' ')}</ErrorBox>
      )}
      {checkFailed && (
        <div className="flex gap-2.5">
          <Button asChild variant="outline" className={SECONDARY}>
            <a href={MODELS_DOCS} target="_blank" rel="noopener">
              Which models work
              <ExternalLink aria-hidden="true" className="size-3.5" />
            </a>
          </Button>
        </div>
      )}
      <MetaLine
        left={
          <>
            <Link2 aria-hidden="true" />
            This link is used up
          </>
        }
        right="Nothing taken from your storage"
      />
    </>
  );
}
