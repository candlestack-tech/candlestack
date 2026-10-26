import { useState } from 'react';

import { ApiError, errorMessage, type Manifest, type Model, type StorageUsage } from '@/api';

import { frameworkOfFile, type Framework } from './frameworks';
import { formatSize } from './format';
import { useModels, useSetManifest, useUploadModel } from './useModels';

// Why the API did not keep a file: one of its checks failed (`opens`, `weights`), it is too
// large, the storage is full, another upload of the user's is still running (`busy`), the user
// has as many models as allowed (`count`), the upload took too long (`timeout`), or something
// else went wrong on the way. Before any of that, the app turns away what is no Keras or ONNX
// file: a model of a framework that exports to ONNX (`export`) or any other file (`unsupported`).
type DetailKind = 'opens' | 'weights' | 'large' | 'busy' | 'count' | 'timeout' | 'other';
export type UploadFailure =
  | { kind: DetailKind; detail: string }
  | { kind: 'storage'; used: number; quota: number }
  | { kind: 'unsupported' }
  | { kind: 'export'; framework: Framework };

// Why a manifest was not kept: the file is no JSON, it is larger than the API takes, a check
// failed (one sentence for each thing wrong, and how many more there are), or the request failed.
export type ManifestFailure =
  | { kind: 'json' }
  | { kind: 'large'; detail: string }
  | { kind: 'check'; problems: string[]; more: number }
  | { kind: 'other'; detail: string };

// The limits of the API that a failure shows with its own title and the API's words.
const LIMITS: Record<string, DetailKind> = {
  'model-too-large': 'large',
  'upload-in-progress': 'busy',
  'too-many-models': 'count',
  'upload-timeout': 'timeout',
};

// The first step: choose a model file; its checks running; the model kept; or why it was not.
export type ModelStep =
  | { step: 'pick' }
  | { step: 'checking'; file: File }
  | { step: 'kept'; model: Model }
  | { step: 'failed'; file: File; failure: UploadFailure };

// The second: choose its manifest; the manifest's checks running; the manifest kept with the
// model; why it was not; or no manifest for now.
export type ManifestStep =
  | { step: 'pick' }
  | { step: 'checking'; file: File }
  | { step: 'kept'; file: File; manifest: Manifest }
  | { step: 'failed'; file: File; failure: ManifestFailure }
  | { step: 'skipped' };

const field = (body: unknown, key: string): unknown =>
  typeof body === 'object' && body !== null ? (body as Record<string, unknown>)[key] : undefined;

const numberIn = (body: unknown, key: string): number | undefined => {
  const value = field(body, key);
  return typeof value === 'number' ? value : undefined;
};

function storageFailure(body: unknown, usage: StorageUsage | undefined): UploadFailure {
  return {
    kind: 'storage',
    used: numberIn(body, 'used_bytes') ?? usage?.used_bytes ?? 0,
    quota: numberIn(body, 'quota_bytes') ?? usage?.quota_bytes ?? 0,
  };
}

const otherFailure = (error: unknown): { kind: 'other'; detail: string } => ({
  kind: 'other',
  detail: error instanceof Error ? error.message : String(error),
});

// The upload's own limits; a proxy in front of the API can also refuse a large body with a bare 413.
function limitFailure(error: ApiError, detail: string): UploadFailure {
  if (error.status === 413) return { kind: 'large', detail };
  return { kind: LIMITS[error.problem ?? ''] ?? 'other', detail };
}

// `usage`: the storage as the app last heard it, for a storage-full answer without its numbers.
export function failureOf(error: unknown, usage?: StorageUsage): UploadFailure {
  if (!(error instanceof ApiError)) return otherFailure(error);
  const detail = errorMessage(error, 'The file could not be sent. Rename it and try again.');
  if (error.problem === 'unsupported-model-format') return { kind: 'unsupported' };
  if (error.problem === 'model-check-failed') {
    return { kind: field(error.body, 'check') === 'weights' ? 'weights' : 'opens', detail };
  }
  if (error.problem === 'storage-full') return storageFailure(error.body, usage);
  return limitFailure(error, detail);
}

// The sentences of a failed manifest check (`problems`), or its detail when the answer has none.
function problemsOf(error: ApiError): string[] {
  const problems = field(error.body, 'problems');
  const details = Array.isArray(problems)
    ? problems.map((problem) => field(problem, 'detail')).filter((detail) => typeof detail === 'string')
    : [];
  return details.length > 0 ? details : [error.message];
}

export function manifestFailureOf(error: unknown): ManifestFailure {
  if (!(error instanceof ApiError)) return otherFailure(error);
  if (error.problem === 'manifest-check-failed') {
    return { kind: 'check', problems: problemsOf(error), more: numberIn(error.body, 'more_problems') ?? 0 };
  }
  if (error.problem === 'manifest-too-large' || error.status === 413) return { kind: 'large', detail: error.message };
  return { kind: 'other', detail: errorMessage(error, 'The manifest could not be sent. Try again.') };
}

// The largest manifest the API keeps (binary, as its limit).
const MANIFEST_MAX_BYTES = 1024 * 1024;

// What the app takes as a model file: Keras and ONNX. The other frameworks export to ONNX, and any
// other file is no model the app takes.
function refusedFormat(file: File): UploadFailure | null {
  const framework = frameworkOfFile(file.name);
  if (framework === 'Keras' || framework === 'ONNX') return null;
  if (framework) return { kind: 'export', framework };
  return { kind: 'unsupported' };
}

// The file's JSON, or undefined when it is no JSON.
async function readJson(file: File): Promise<unknown> {
  try {
    return JSON.parse(await file.text()) as unknown;
  } catch {
    return undefined;
  }
}

// Step 2: reads the manifest file, then sends it for the model (the model the API answers goes to
// `onKept`). A file the API would refuse anyway (too large, no JSON) is said at once.
function useManifestStep(onKept: (model: Model) => void) {
  const [state, setState] = useState<ManifestStep>({ step: 'pick' });
  const saveManifest = useSetManifest();

  const choose = async (model: Model, file: File) => {
    const fail = (failure: ManifestFailure) => setState({ step: 'failed', file, failure });
    if (file.size > MANIFEST_MAX_BYTES) {
      fail({ kind: 'large', detail: `The file is ${formatSize(file.size)}.` });
      return;
    }
    setState({ step: 'checking', file });
    const json = await readJson(file);
    if (json === undefined) {
      fail({ kind: 'json' });
      return;
    }
    try {
      onKept(await saveManifest(model.id, json));
      setState({ step: 'kept', file, manifest: json as Manifest });
    } catch (error) {
      fail(manifestFailureOf(error));
    }
  };

  return { state, setState, choose };
}

// The two steps of adding a model (mockups/app, the upload in three steps): the model file goes to
// the API at once, then its manifest. Given a model (Add manifest), the first step is done. An
// upload does not stop when the dialog closes: once the file is in, the API checks and keeps it
// whatever the page does, so it runs to its answer, which puts a kept model on the page
// (useUploadModel); a manifest likewise.
export function useUpload(existing?: Model) {
  const [model, setModel] = useState<ModelStep>(existing ? { step: 'kept', model: existing } : { step: 'pick' });
  const manifest = useManifestStep((kept) => setModel({ step: 'kept', model: kept }));
  const upload = useUploadModel();
  const usage = useModels().data?.usage;

  const chooseModel = (file: File) => {
    const refused = refusedFormat(file);
    if (refused) {
      setModel({ step: 'failed', file, failure: refused });
      return;
    }
    // Too large for the API to take: said at once, without sending 50 MB first.
    if (usage && file.size > usage.file_max_bytes) {
      setModel({ step: 'failed', file, failure: { kind: 'large', detail: `The file is ${formatSize(file.size)}.` } });
      return;
    }
    setModel({ step: 'checking', file });
    upload(file).then(
      (kept) => setModel({ step: 'kept', model: kept }),
      (error: unknown) => setModel({ step: 'failed', file, failure: failureOf(error, usage) }),
    );
  };

  return {
    model,
    manifest: manifest.state,
    chooseModel,
    chooseManifest: async (file: File) => {
      if (model.step === 'kept') await manifest.choose(model.model, file);
    },
    resetModel: () => setModel({ step: 'pick' }),
    resetManifest: () => manifest.setState({ step: 'pick' }),
    skipManifest: () => manifest.setState({ step: 'skipped' }),
  };
}
