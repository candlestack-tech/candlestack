import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import {
  ApiError,
  modelsQueryOptions,
  uploadByLink,
  uploadLinkQueryOptions,
  type Model,
  type ModelUpload,
  type StorageUsage,
} from '@/api';

import { frameworkOfFile, type Framework } from './frameworks';
import { useModels } from './useModels';
import { failureOf, type UploadFailure } from './useUpload';

// The upload page of a one-time link (design/mockups/api-mcp, #upload/*): the link the API reads
// by its token, then the file the user drops, sent to the link with its progress, checked, and
// the model it made or why it failed.

// What the page says above the drop zone: a file that is not the one the link is for (refused in
// the browser, so the link stays usable), or an upload the API turned away before it started.
export type ReadyNote =
  | { kind: 'wrong-file'; file: File; expected: ModelUpload; reason: 'size' | 'format' }
  | { kind: 'refused'; title: string; detail: string };

// Where this page's own upload stands. Before any, the link's status decides the page.
export type Phase =
  | { step: 'ready'; note?: ReadyNote }
  | { step: 'uploading'; file: File; sent: number; total: number }
  | { step: 'checking'; file: File }
  | { step: 'done'; model: Model; usedBefore: number | undefined }
  | { step: 'failed'; failure: UploadFailure }
  | { step: 'cancelled' };

// The framework the link's name says, which the file must be of too.
export const linkFramework = (link: ModelUpload): Framework | null => frameworkOfFile(link.name);

// Why `file` is not the one the link was made for: another format, or another size. The name may
// differ (a renamed copy): the model gets the link's name anyway.
export function wrongFile(file: File, link: ModelUpload): 'size' | 'format' | null {
  if (frameworkOfFile(file.name) !== linkFramework(link)) return 'format';
  return file.size === link.size_bytes ? null : 'size';
}

// Refused before the link was used: it can take the file again.
const BEFORE_START: Record<string, string> = {
  'upload-in-progress': 'Another upload is in progress',
  'larger-than-link': 'Larger than the link',
};

// The time now, again every `every` ms, for the link's countdown and the "2 min ago".
export function useNow(every = 15_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(timer);
  }, [every]);
  return now;
}

// What a link that failed before this page opened recorded, as the failure of an upload.
export function storedFailure(link: ModelUpload, usage: StorageUsage | undefined): UploadFailure {
  if (link.problem === 'model-check-failed') {
    return { kind: link.check === 'weights' ? 'weights' : 'opens', detail: link.error ?? '' };
  }
  if (link.problem === 'storage-full' && usage)
    return { kind: 'storage', used: usage.used_bytes, quota: usage.quota_bytes };
  return { kind: 'other', detail: link.error ?? 'The upload did not finish. Ask your agent for a new link.' };
}

const CHECKING_POLL_MS = 2000;

// Where the page goes when the upload failed with `error`: back to the drop zone with why, when
// the API turned it away before the link was used; to the link's own state (`ready`, which shows
// it) when the link was used or expired meanwhile; else the failure. Null for Cancel's abort.
function phaseAfter(error: unknown, usage: StorageUsage | undefined): Phase | null {
  if (error instanceof DOMException && error.name === 'AbortError') return null;
  const title = error instanceof ApiError && error.problem ? BEFORE_START[error.problem] : undefined;
  if (error instanceof ApiError && title)
    return { step: 'ready', note: { kind: 'refused', title, detail: error.message } };
  if (error instanceof ApiError && (error.status === 404 || error.status === 410)) return { step: 'ready' };
  return { step: 'failed', failure: failureOf(error, usage) };
}

// The link of the token, asked again while another tab's upload through it is checked.
function useLink(token: string) {
  return useQuery({
    ...uploadLinkQueryOptions(token),
    refetchInterval: (query) => (query.state.data?.status === 'checking' ? CHECKING_POLL_MS : false),
  });
}

// Asks again for the link and the storage, as the API has them after an upload.
function useRefresh(token: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: uploadLinkQueryOptions(token).queryKey });
    void queryClient.invalidateQueries({ queryKey: modelsQueryOptions().queryKey });
  };
}

export function useLinkUpload(token: string) {
  const link = useLink(token);
  const usage = useModels().data?.usage;
  const refresh = useRefresh(token);
  const [phase, setPhase] = useState<Phase>({ step: 'ready' });
  // Leaving the page does not abort the upload: once it started, the link is used anyway.
  const abort = useRef<AbortController | null>(null);

  const send = (file: File) => {
    const usedBefore = usage?.used_bytes;
    const controller = new AbortController();
    abort.current = controller;
    setPhase({ step: 'uploading', file, sent: 0, total: file.size });
    const onProgress = (sent: number, total: number) => {
      if (!controller.signal.aborted)
        setPhase(sent >= total ? { step: 'checking', file } : { step: 'uploading', file, sent, total });
    };
    uploadByLink(token, file, { signal: controller.signal, onProgress })
      .then(
        (model) => setPhase({ step: 'done', model, usedBefore }),
        (error: unknown) => {
          const next = phaseAfter(error, usage);
          if (next) setPhase(next);
        },
      )
      .finally(refresh);
  };

  const choose = (file: File) => {
    const expected = link.data;
    if (!expected) return;
    const reason = wrongFile(file, expected);
    if (reason) setPhase({ step: 'ready', note: { kind: 'wrong-file', file, expected, reason } });
    else send(file);
  };

  // Cancel: the link was used once the upload started, so it cannot take the file again.
  const cancel = () => {
    abort.current?.abort();
    setPhase({ step: 'cancelled' });
  };

  return { link, usage, phase, choose, cancel };
}
