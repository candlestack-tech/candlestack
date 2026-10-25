import { queryOptions } from '@tanstack/react-query';

import { del, get, patch, postFile, put } from './client';
import type { components } from './schema';

export type Model = components['schemas']['ModelOut'];
export type Models = components['schemas']['ModelsOut'];
export type ModelState = components['schemas']['ModelState'];
export type ManifestSummary = components['schemas']['ManifestSummary'];
export type Manifest = components['schemas']['Manifest'];
export type StorageUsage = components['schemas']['StorageUsage'];
export type ModelUpdate = components['schemas']['ModelUpdate'];
export type Catalog = components['schemas']['CatalogOut'];
export type CatalogBlock = components['schemas']['BlockOut'];

// GET /api/v1/models: the user's models, oldest first, and the storage they take. Under
// ['account', ...] like the user, so signing out knows to forget them. The page and the sidebar
// share it; every change to the models updates this one entry.
export function modelsQueryOptions() {
  return queryOptions({
    queryKey: ['account', 'models'],
    queryFn: ({ signal }) => get<Models>('/v1/models', signal),
  });
}

const modelPath = (id: string) => `/v1/models/${encodeURIComponent(id)}`;

// POST /api/v1/models?name=...: the file itself is the body. The API checks it before it keeps
// it; a failed check is an ApiError with the problem model-check-failed and `check` in its body.
export function uploadModel(file: File): Promise<Model> {
  const query = new URLSearchParams({ name: file.name }).toString();
  return postFile<Model>(`/v1/models?${query}`, file);
}

// PATCH /api/v1/models/{id}: renames (the name without its extension) or stars the model.
export function updateModel(id: string, update: ModelUpdate): Promise<Model> {
  return patch<Model>(modelPath(id), update);
}

// DELETE /api/v1/models/{id}: the model and its file go, which frees its storage.
export function deleteModel(id: string): Promise<void> {
  return del(modelPath(id));
}

// GET /api/v1/models/{id}/manifest: the manifest as it was given, its example too (the list
// leaves the example out). Not under ['account', 'models'], so asking for the list again does not
// fetch every manifest the app has seen; under ['account', ...], so signing out forgets it.
export function manifestQueryOptions(id: string) {
  return queryOptions({
    queryKey: ['account', 'manifest', id],
    queryFn: ({ signal }) => get<Manifest>(`${modelPath(id)}/manifest`, signal),
  });
}

// PUT /api/v1/models/{id}/manifest: adds the manifest or replaces the one the model had. The API
// keeps it only when every check passed and answers with the model, now `ready`; else an ApiError
// with the problem manifest-check-failed and `problems` in its body. `manifest` is the file's JSON
// as it was read: the API checks its form.
export function setManifest(id: string, manifest: unknown): Promise<Model> {
  return put<Model>(`${modelPath(id)}/manifest`, manifest);
}

// GET /api/v1/preprocessing/catalog: every block a manifest can name, with what it is and its
// display group. It changes only with a release, so the app asks once.
export function catalogQueryOptions() {
  return queryOptions({
    queryKey: ['catalog'],
    queryFn: ({ signal }) => get<Catalog>('/v1/preprocessing/catalog', signal),
    staleTime: Infinity,
  });
}
