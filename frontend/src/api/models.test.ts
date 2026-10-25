import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CATALOG, catalogAnswer, manifest, model, modelsAnswer, problem, stubApi } from '@/test/api';

import { ApiError } from './client';
import {
  catalogQueryOptions,
  deleteModel,
  manifestQueryOptions,
  modelsQueryOptions,
  setManifest,
  updateModel,
  uploadModel,
} from './models';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('modelsQueryOptions', () => {
  it('reads the models and the storage, under the account so signing out forgets them', async () => {
    stubApi(modelsAnswer([model()]));
    const options = modelsQueryOptions();
    expect(options.queryKey).toEqual(['account', 'models']);
    const data = await new QueryClient().fetchQuery(options);
    expect(data.models).toEqual([model()]);
    expect(data.usage.quota_bytes).toBe(100 * 1024 * 1024);
  });
});

describe('uploadModel', () => {
  it('posts the file as the raw body, its name in the query', async () => {
    const fetch = stubApi({ 'POST /api/v1/models?name=lstm+signals.keras': { status: 201, body: model() } });
    const file = new File(['weights'], 'lstm signals.keras');
    await expect(uploadModel(file)).resolves.toEqual(model());
    const init = fetch.mock.calls[0]?.[1];
    expect(init?.body).toBe(file);
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/octet-stream' });
  });

  it('passes a failed check on with its slug and its check', async () => {
    stubApi({
      'POST /api/v1/models?name=a.keras': problem(422, 'model-check-failed', 'No weights.', { check: 'weights' }),
    });
    const error: unknown = await uploadModel(new File([''], 'a.keras')).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, problem: 'model-check-failed', body: { check: 'weights' } });
  });
});

describe('updateModel', () => {
  it('patches the name or the star', async () => {
    const fetch = stubApi({ 'PATCH /api/v1/models/mdl_7f3k2m9qx4': { status: 200, body: model({ starred: true }) } });
    await expect(updateModel('mdl_7f3k2m9qx4', { starred: true })).resolves.toMatchObject({ starred: true });
    expect(fetch.mock.calls[0]?.[1]?.body).toBe('{"starred":true}');
  });

  it('passes a taken name on as model-name-taken', async () => {
    stubApi({ 'PATCH /api/v1/models/mdl_7f3k2m9qx4': problem(409, 'model-name-taken', 'Taken.') });
    await expect(updateModel('mdl_7f3k2m9qx4', { name: 'lstm' })).rejects.toMatchObject({
      problem: 'model-name-taken',
    });
  });
});

describe('deleteModel', () => {
  it('deletes the model', async () => {
    const fetch = stubApi({ 'DELETE /api/v1/models/mdl_7f3k2m9qx4': { status: 204 } });
    await expect(deleteModel('mdl_7f3k2m9qx4')).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledOnce();
  });
});

describe('manifestQueryOptions', () => {
  it("reads a model's whole manifest under the account, apart from the list", async () => {
    stubApi({ 'GET /api/v1/models/mdl_7f3k2m9qx4/manifest': { status: 200, body: manifest() } });
    const options = manifestQueryOptions('mdl_7f3k2m9qx4');
    expect(options.queryKey).toEqual(['account', 'manifest', 'mdl_7f3k2m9qx4']);
    await expect(new QueryClient().fetchQuery(options)).resolves.toEqual(manifest());
  });

  it('passes a model without one on as no-manifest', async () => {
    stubApi({ 'GET /api/v1/models/mdl_7f3k2m9qx4/manifest': problem(404, 'no-manifest', 'No manifest yet.') });
    await expect(new QueryClient().fetchQuery(manifestQueryOptions('mdl_7f3k2m9qx4'))).rejects.toMatchObject({
      status: 404,
      problem: 'no-manifest',
    });
  });
});

describe('setManifest', () => {
  it('puts the manifest as JSON and answers with the model', async () => {
    const ready = model({ state: 'ready' });
    const fetch = stubApi({ 'PUT /api/v1/models/mdl_7f3k2m9qx4/manifest': { status: 200, body: ready } });
    await expect(setManifest('mdl_7f3k2m9qx4', manifest())).resolves.toEqual(ready);
    const init = fetch.mock.calls[0]?.[1];
    expect(init?.body).toBe(JSON.stringify(manifest()));
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json' });
  });

  it('passes a failed check on with its problems', async () => {
    const problems = [{ check: 'model', detail: 'The manifest gives [24, 5], the model takes [25, 5].' }];
    stubApi({
      'PUT /api/v1/models/mdl_7f3k2m9qx4/manifest': problem(422, 'manifest-check-failed', 'Wrong.', { problems }),
    });
    await expect(setManifest('mdl_7f3k2m9qx4', {})).rejects.toMatchObject({
      problem: 'manifest-check-failed',
      body: { problems },
    });
  });
});

describe('catalogQueryOptions', () => {
  it('reads the catalog once', async () => {
    stubApi(catalogAnswer);
    const options = catalogQueryOptions();
    expect(options.staleTime).toBe(Infinity);
    await expect(new QueryClient().fetchQuery(options)).resolves.toEqual(CATALOG);
  });
});
