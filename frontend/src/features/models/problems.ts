import { formatUsage } from './format';
import type { ManifestFailure, UploadFailure } from './useUpload';

// What a failed step of the upload says in its place (mockups/app, dErr): what is wrong, then
// the details, one per line.
export type Problem = { title: string; lines: string[] };

const TAKEN = 'CandleStack takes Keras (.keras) and ONNX (.onnx) models.';

type Of<K extends UploadFailure['kind']> = Extract<UploadFailure, { kind: K }>;

const detail = (title: string) => (failure: { detail: string }) => ({ title, lines: [failure.detail] });

const MODEL_PROBLEMS: { [K in UploadFailure['kind']]: (failure: Of<K>) => Problem } = {
  export: (failure) => ({
    title: `${failure.framework} files are not supported`,
    lines: [`Export the model to ONNX. ${TAKEN}`],
  }),
  unsupported: () => ({ title: 'This file is not supported', lines: [TAKEN] }),
  opens: detail('Does not open as a safe model'),
  weights: detail('No trained weights in the file'),
  large: detail('Larger than 50 MB'),
  storage: (failure) => ({
    title: 'Not enough storage',
    lines: [`${formatUsage(failure.used, failure.quota)} used. Delete a model to free space.`],
  }),
  busy: detail('Another upload is in progress'),
  count: detail('Too many models'),
  timeout: detail('Upload timed out'),
  other: detail('Upload failed'),
};

export function modelProblem(failure: UploadFailure): Problem {
  const describe = MODEL_PROBLEMS[failure.kind] as (failure: UploadFailure) => Problem;
  return describe(failure);
}

// A failed check names what is wrong in its first sentence; the others follow, then how many more.
export function manifestProblem(failure: ManifestFailure): Problem {
  switch (failure.kind) {
    case 'json':
      return { title: 'This file is not JSON', lines: ['Choose the manifest, a .json file.'] };
    case 'large':
      return { title: 'Larger than 1 MB', lines: [failure.detail] };
    case 'check': {
      const [first = 'The manifest check failed.', ...rest] = failure.problems;
      const more = failure.more > 0 ? [`And ${failure.more} more ${failure.more === 1 ? 'problem' : 'problems'}.`] : [];
      return { title: first, lines: [...rest, ...more] };
    }
    case 'other':
      return { title: 'Upload failed', lines: [failure.detail] };
  }
}
