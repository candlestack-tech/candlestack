import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, del, errorMessage, get, parseRetryAfter, patch, post, postFile, put } from './client';

function answer(status: number, body: string | null, headers: Record<string, string> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(new Response(body, { status, headers }))),
  );
}

async function errorOf(promise: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await promise.then(
    () => undefined,
    (reason: unknown) => reason,
  );
  if (!(error instanceof ApiError)) throw new Error(`expected an ApiError, got ${String(error)}`);
  return error;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('get', () => {
  it('returns the JSON body of a 2xx', async () => {
    answer(200, '{"status":"ok"}', { 'Content-Type': 'application/json' });
    await expect(get('/health')).resolves.toEqual({ status: 'ok' });
    expect(fetch).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({ headers: { Accept: 'application/json' } }),
    );
  });

  it('maps problem+json to an ApiError with detail, slug and Retry-After', async () => {
    const problem = {
      type: 'https://candlestack.tech/problems/rate-limited',
      title: 'Too many requests',
      status: 429,
      detail: 'Over 60 requests this minute.',
    };
    answer(429, JSON.stringify(problem), { 'Retry-After': '17' });
    const error = await errorOf(get('/data/candles'));
    expect(error).toMatchObject({ status: 429, message: problem.detail, problem: 'rate-limited', retryAfter: 17 });
    expect(error.body).toEqual(problem);
  });

  it('falls back to the title, then to the status, for the message', async () => {
    answer(404, '{"title":"Not found"}');
    expect((await errorOf(get('/x'))).message).toBe('Not found');
    answer(502, '<html>Bad gateway</html>');
    const error = await errorOf(get('/x'));
    expect(error).toMatchObject({ status: 502, message: 'The server answered 502.', body: null });
  });

  it('throws an ApiError with status 0 when there is no answer', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    );
    const error = await errorOf(get('/health'));
    expect(error.status).toBe(0);
    expect(error.retryAfter).toBeUndefined();
  });

  it('passes an abort through unchanged', async () => {
    const controller = new AbortController();
    controller.abort();
    const abort = new DOMException('Aborted', 'AbortError');
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(abort)),
    );
    await expect(get('/health', controller.signal)).rejects.toBe(abort);
  });

  it('throws instead of returning null when a 2xx is not JSON', async () => {
    answer(200, '<!doctype html><title>CandleStack</title>', { 'Content-Type': 'text/html' });
    const error = await errorOf(get('/health'));
    expect(error.status).toBe(200);
    expect(error.message).toMatch(/cannot read/);
  });
});

describe('post', () => {
  it('sends a POST and resolves on 204 No Content', async () => {
    answer(204, null);
    await expect(post('/v1/auth/sign-out')).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/auth/sign-out',
      expect.objectContaining({ method: 'POST', headers: { Accept: 'application/json' } }),
    );
  });

  it('maps an error answer to an ApiError like get', async () => {
    answer(401, '{"type":"https://candlestack.tech/problems/not-signed-in","detail":"Sign in first."}');
    const error = await errorOf(post('/v1/auth/sign-out'));
    expect(error).toMatchObject({ status: 401, message: 'Sign in first.', problem: 'not-signed-in' });
  });

  it('throws an ApiError with status 0 when there is no answer', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    );
    expect((await errorOf(post('/v1/auth/sign-out'))).status).toBe(0);
  });
});

describe('del', () => {
  it('sends a DELETE and resolves on 204 No Content', async () => {
    answer(204, null);
    await expect(del('/v1/me')).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith('/api/v1/me', expect.objectContaining({ method: 'DELETE' }));
  });

  it('maps an error answer to an ApiError', async () => {
    answer(409, '{"type":"https://candlestack.tech/problems/last-sign-in-method","detail":"Keep one."}');
    expect(await errorOf(del('/v1/me/sign-in-methods/github'))).toMatchObject({
      status: 409,
      problem: 'last-sign-in-method',
    });
  });
});

describe('patch and put', () => {
  it('send the body as JSON and return the JSON answer', async () => {
    for (const [send, method] of [
      [patch, 'PATCH'],
      [put, 'PUT'],
    ] as const) {
      answer(200, '{"display_name":"Ada"}');
      await expect(send('/v1/me', { display_name: 'Ada' })).resolves.toEqual({ display_name: 'Ada' });
      expect(fetch).toHaveBeenCalledWith('/api/v1/me', {
        method,
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: '{"display_name":"Ada"}',
      });
    }
  });

  it('map an error answer to an ApiError', async () => {
    answer(422, '{"title":"Unprocessable"}');
    expect(await errorOf(patch('/v1/me', {}))).toMatchObject({ status: 422, message: 'Unprocessable' });
  });
});

describe('postFile', () => {
  it('sends the file as the raw body and returns the JSON answer', async () => {
    answer(201, '{"id":"mdl_a"}');
    const file = new Blob(['weights']);
    await expect(postFile('/v1/models?name=a.keras', file)).resolves.toEqual({ id: 'mdl_a' });
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/models?name=a.keras',
      expect.objectContaining({
        method: 'POST',
        body: file,
        headers: { Accept: 'application/json', 'Content-Type': 'application/octet-stream' },
      }),
    );
  });

  it('maps an error answer to an ApiError with its body', async () => {
    const problem = { type: 'https://candlestack.tech/problems/storage-full', detail: 'Full.', used_bytes: 5 };
    answer(409, JSON.stringify(problem));
    const error = await errorOf(postFile('/v1/models?name=a.keras', new Blob([])));
    expect(error).toMatchObject({ status: 409, problem: 'storage-full', body: problem });
  });
});

describe('errorMessage', () => {
  it("says the page's own words instead of the API's check of the request", () => {
    const validation = new ApiError(422, "body 'name': String should match pattern '^[^/]+$'", {
      problem: 'validation',
    });
    expect(errorMessage(validation, 'Could not save.')).toBe('Could not save.');
  });

  it('keeps the plain words of any other problem', () => {
    expect(errorMessage(new ApiError(409, 'Keep one.', { problem: 'last-sign-in-method' }), 'x')).toBe('Keep one.');
    expect(errorMessage(new ApiError(0, 'Cannot reach the server.'), 'x')).toBe('Cannot reach the server.');
    expect(errorMessage(new Error('Boom.'), 'x')).toBe('Boom.');
  });
});

describe('parseRetryAfter', () => {
  const now = Date.parse('2026-10-08T12:00:00Z');

  it('reads whole seconds, including 0', () => {
    expect(parseRetryAfter('30', now)).toBe(30);
    expect(parseRetryAfter('0', now)).toBe(0);
    expect(parseRetryAfter(' 5 ', now)).toBe(5);
  });

  it('reads an HTTP date as the seconds until it, never below 0', () => {
    expect(parseRetryAfter('Thu, 08 Oct 2026 12:00:42 GMT', now)).toBe(42);
    expect(parseRetryAfter('Thu, 08 Oct 2026 11:59:00 GMT', now)).toBe(0);
  });

  it('treats a missing or malformed value as no hint', () => {
    expect(parseRetryAfter(null, now)).toBeUndefined();
    expect(parseRetryAfter('', now)).toBeUndefined();
    expect(parseRetryAfter('soon', now)).toBeUndefined();
    expect(parseRetryAfter('-5', now)).toBeUndefined();
    expect(parseRetryAfter('1.5', now)).toBeUndefined();
  });
});
