import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import {
  ApiError,
  deleteModel,
  manifestQueryOptions,
  modelsQueryOptions,
  setManifest,
  updateModel,
  uploadModel,
  type Manifest,
  type Model,
  type Models,
} from '@/api';

const KEY = modelsQueryOptions().queryKey;

// The user's models and their storage, shared by the page and the sidebar.
export function useModels() {
  return useQuery(modelsQueryOptions());
}

// A model's whole manifest, its example too; nothing to ask for while it has none.
export function useManifest(model: Model | undefined) {
  return useQuery({
    ...manifestQueryOptions(model?.id ?? ''),
    enabled: model !== undefined && model.state !== 'needs_setup',
  });
}

// Puts the API's answer into the one models entry at once, then asks again for the storage
// numbers the API counts itself, without waiting for that answer.
function change(queryClient: QueryClient, update: (data: Models) => Models) {
  queryClient.setQueryData(KEY, (data) => (data ? update(data) : data));
  void queryClient.invalidateQueries({ queryKey: KEY });
}

const replace = (data: Models, model: Model): Models => ({
  ...data,
  models: data.models.map((item) => (item.id === model.id ? model : item)),
});

const refused = (error: unknown) => error instanceof ApiError && error.status >= 400 && error.status < 500;

// Uploads a file; the new model joins the end of the list (oldest first) and takes its size of
// the storage. Not a mutation: the dialog keeps the upload's own state. The list follows the
// answer even when the dialog closed before it came.
export function useUploadModel() {
  const queryClient = useQueryClient();
  return async (file: File): Promise<Model> => {
    let model: Model;
    try {
      model = await uploadModel(file);
    } catch (error) {
      // Without an answer the app can read (a connection lost on the way back, a server error),
      // the API may have kept the file: the list is asked for again. A refusal (4xx) kept nothing.
      if (!refused(error)) void queryClient.invalidateQueries({ queryKey: KEY });
      throw error;
    }
    change(queryClient, (data) => ({
      models: [...data.models, model],
      usage: { ...data.usage, used_bytes: data.usage.used_bytes + model.size_bytes },
    }));
    return model;
  };
}

// Adds a model's manifest: the model the API answers (now `ready`) takes its place in the list,
// and the manifest as it was sent is the one the model's page reads. Not a mutation, like the
// upload: the dialog keeps its own state, and the list follows the answer even when the dialog
// closed before it came.
export function useSetManifest() {
  const queryClient = useQueryClient();
  return async (id: string, manifest: unknown): Promise<Model> => {
    let model: Model;
    try {
      model = await setManifest(id, manifest);
    } catch (error) {
      forgetIfGone(queryClient, error);
      throw error;
    }
    queryClient.setQueryData(manifestQueryOptions(id).queryKey, manifest as Manifest);
    change(queryClient, (data) => replace(data, model));
    return model;
  };
}

// Stars or unstars at once; the star goes back when the API refuses.
export function useStarModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (model: Model) => updateModel(model.id, { starred: !model.starred }),
    onMutate: async (model) => {
      await queryClient.cancelQueries({ queryKey: KEY });
      const before = queryClient.getQueryData(KEY);
      queryClient.setQueryData(KEY, (data) => data && replace(data, { ...model, starred: !model.starred }));
      return { before };
    },
    onError: (_error, _model, context) => queryClient.setQueryData(KEY, context?.before),
    onSettled: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
}

// The model is gone already (deleted in another tab, say): the API answers 404 not-found.
export const isGone = (error: unknown) => error instanceof ApiError && error.status === 404;

// A model that is gone leaves the list: the list is asked for again.
function forgetIfGone(queryClient: QueryClient, error: unknown) {
  if (isGone(error)) void queryClient.invalidateQueries({ queryKey: KEY });
}

// Renames a model: `name` is the part before the extension, which stays.
export function useRenameModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updateModel(id, { name }),
    onSuccess: (model) => change(queryClient, (data) => replace(data, model)),
    onError: (error) => forgetIfGone(queryClient, error),
  });
}

// Deletes a model, which frees its size of the storage.
export function useDeleteModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (model: Model) => deleteModel(model.id),
    onSuccess: (_answer, model) =>
      change(queryClient, (data) => ({
        models: data.models.filter((item) => item.id !== model.id),
        usage: { ...data.usage, used_bytes: Math.max(0, data.usage.used_bytes - model.size_bytes) },
      })),
    onError: (error) => forgetIfGone(queryClient, error),
  });
}
