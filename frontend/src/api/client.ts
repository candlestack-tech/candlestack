// The only place that calls fetch. Every request to the CandleStack API goes through here, so
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

function errorFromResponse(res: Response, body: unknown): ApiError {
  const problem: Problem = typeof body === 'object' && body !== null ? body : {};
  const text = (value: unknown) => (typeof value === 'string' && value !== '' ? value : undefined);
  const message = text(problem.detail) ?? text(problem.title) ?? `The server answered ${res.status}.`;
  const slug = text(problem.type)?.split('/problems/')[1];
  return new ApiError(res.status, message, {
    body,
    retryAfter: parseRetryAfter(res.headers.get('Retry-After')),
    problem: slug,
  });
}

async function readJson(res: Response): Promise<{ ok: true; body: unknown } | { ok: false }> {
  try {
    return { ok: true, body: await res.json() };
  } catch {
    return { ok: false };
  }
}

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
    throw new ApiError(0, 'Cannot reach the server. Check your connection.');
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
  if (!json.ok) throw new ApiError(res.status, 'The server sent an answer the app cannot read.');
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
