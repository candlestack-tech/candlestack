// The only place that calls fetch (and XMLHttpRequest, for an upload's progress). Every request to the CandleStack API goes through here, so
// when the API changes (#104-#109) the fix is in src/api/ and nowhere else.

// Relative on purpose: in prod the page and the API share a host, in dev the Vite proxy
// forwards /api to https://app.candlestack.tech (vite.config.ts).
const BASE = '/api';

// An error answer of the API, or no answer at all (status 0). Errors are RFC 9457
// problem+json: `detail` says in plain words what went wrong and is fine to show to the user.
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  // Seconds to wait, from the Retry-After header (429 rate-limited, 503 source-unavailable).
  readonly retryAfter?: number;
  // The problem slug, e.g. "rate-limited", from https://candlestack.tech/problems/<slug>.
  readonly problem?: string;

  constructor(
    status: number,
    message: string,
    details: { body?: unknown; retryAfter?: number; problem?: string } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = details.body ?? null;
    this.retryAfter = details.retryAfter;
    this.problem = details.problem;
  }
}

// What to show for a failed request. The API's own check of a request (422 `validation`) names
// its fields and rules ("body 'name': String should match pattern ..."), which tells the user
// nothing: the page says in `fallback` what did not work instead.
export function errorMessage(error: Error, fallback: string): string {
  return error instanceof ApiError && error.problem === 'validation' ? fallback : error.message;
}

// Retry-After is either whole seconds ("30", "0") or an HTTP date. Anything else: no hint.
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | undefined {
  if (value === null || value.trim() === '') return undefined;
  const text = value.trim();
  if (/^\d+$/.test(text)) return Number(text);
  // An HTTP date has a day and a month name; without letters, Date.parse would also accept "-5".
  if (!/[a-z]/i.test(text)) return undefined;
  const date = Date.parse(text);
  if (Number.isNaN(date)) return undefined;
  return Math.max(0, Math.ceil((date - now) / 1000));
}

type Problem = { type?: unknown; title?: unknown; detail?: unknown };

// The error of an answer with `status`, its JSON body (null when it had none) and its Retry-After.
function problemError(status: number, body: unknown, retryAfter: string | null): ApiError {
  const problem: Problem = typeof body === 'object' && body !== null ? body : {};
  const text = (value: unknown) => (typeof value === 'string' && value !== '' ? value : undefined);
  const message = text(problem.detail) ?? text(problem.title) ?? `The server answered ${status}.`;
  const slug = text(problem.type)?.split('/problems/')[1];
  return new ApiError(status, message, { body, retryAfter: parseRetryAfter(retryAfter), problem: slug });
}

const errorFromResponse = (res: Response, body: unknown) =>
  problemError(res.status, body, res.headers.get('Retry-After'));

type Json = { ok: true; body: unknown } | { ok: false };

async function readJson(res: Response): Promise<Json> {
  try {
    return { ok: true, body: await res.json() };
  } catch {
    return { ok: false };
  }
}

function parseJson(text: string): Json {
  try {
    return { ok: true, body: JSON.parse(text) as unknown };
  } catch {
    return { ok: false };
  }
}

const UNREADABLE = 'The server sent an answer the app cannot read.';
const UNREACHABLE = 'Cannot reach the server. Check your connection.';

// The URL of an API path, e.g. for an <img> that the browser loads itself.
export function apiUrl(path: string): string {
  return BASE + path;
}

// Sends a request and turns a failure into an ApiError: no answer at all (status 0) or an error
// status with its problem. An abort passes through unchanged.
async function send(path: string, init: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), init);
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError(0, UNREACHABLE);
  }
  if (!res.ok) {
    const json = await readJson(res);
    throw errorFromResponse(res, json.ok ? json.body : null);
  }
  return res;
}

// The JSON body of a 2xx. One that is not JSON (an HTML page on an /api path, say) is not data
// the page can use.
async function readBody<T>(res: Response): Promise<T> {
  const json = await readJson(res);
  if (!json.ok) throw new ApiError(res.status, UNREADABLE);
  return json.body as T;
}

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await send(path, { signal, headers: { Accept: 'application/json' } });
  return readBody<T>(res);
}

// A command the API answers without a body (204), e.g. signing out. Its errors are ApiErrors
// like those of get.
export async function post(path: string): Promise<void> {
  await send(path, { method: 'POST', headers: { Accept: 'application/json' } });
}

// A command without a body that the API answers with JSON, e.g. cancelling a queued backtest.
export async function postFor<T>(path: string): Promise<T> {
  const res = await send(path, { method: 'POST', headers: { Accept: 'application/json' } });
  return readBody<T>(res);
}

// A file the API answers with (any type), with its errors as ApiErrors like those of get.
export async function getBlob(path: string): Promise<Blob> {
  const res = await send(path, {});
  return res.blob();
}

// DELETE, answered without a body (204), e.g. deleting the account.
export async function del(path: string): Promise<void> {
  await send(path, { method: 'DELETE', headers: { Accept: 'application/json' } });
}

// A change sent as JSON (PATCH a part, PUT the whole, POST a new one), answered with the
// changed or new resource.
async function sendJson<T>(method: 'PATCH' | 'PUT' | 'POST', path: string, body: unknown): Promise<T> {
  const res = await send(path, {
    method,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return readBody<T>(res);
}

export const patch = <T>(path: string, body: unknown) => sendJson<T>('PATCH', path, body);
export const put = <T>(path: string, body: unknown) => sendJson<T>('PUT', path, body);
export const postJson = <T>(path: string, body: unknown) => sendJson<T>('POST', path, body);

// Sends a file as the raw body, e.g. a model upload, and returns the JSON answer. Raw, not a
// form: the API reads the body as the file and takes its name from the query.
export async function postFile<T>(path: string, file: Blob): Promise<T> {
  const res = await send(path, {
    method: 'POST',
    body: file,
    headers: { Accept: 'application/json', 'Content-Type': 'application/octet-stream' },
  });
  return readBody<T>(res);
}

type Progress = {
  // The bytes sent so far and the whole file's, as the browser reports them while it sends.
  onProgress: (sent: number, total: number) => void;
  // Aborting stops the upload: the promise rejects with the AbortError.
  signal?: AbortSignal;
};

// PUTs a file as the raw body with its progress, which fetch cannot report, and returns the JSON
// answer; its errors are ApiErrors like those of get.
export function putFileWithProgress<T>(path: string, file: Blob, { onProgress, signal }: Progress): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('PUT', apiUrl(path));
    request.setRequestHeader('Accept', 'application/json');
    request.setRequestHeader('Content-Type', 'application/octet-stream');
    request.upload.addEventListener('progress', (event) => {
      onProgress(event.loaded, event.lengthComputable ? event.total : file.size);
    });
    request.addEventListener('load', () => {
      const json = parseJson(request.responseText);
      if (request.status >= 200 && request.status < 300) {
        if (json.ok) resolve(json.body as T);
        else reject(new ApiError(request.status, UNREADABLE));
        return;
      }
      reject(problemError(request.status, json.ok ? json.body : null, request.getResponseHeader('Retry-After')));
    });
    request.addEventListener('error', () => reject(new ApiError(0, UNREACHABLE)));
    request.addEventListener('abort', () => reject(new DOMException('The upload was cancelled.', 'AbortError')));
    if (signal?.aborted) {
      reject(new DOMException('The upload was cancelled.', 'AbortError'));
      return;
    }
    signal?.addEventListener('abort', () => request.abort(), { once: true });
    request.send(file);
  });
}
