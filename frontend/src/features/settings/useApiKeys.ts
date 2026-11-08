import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError, apiKeysQueryOptions, createApiKey, revokeApiKey, type ApiKey, type ApiKeyCreated } from '@/api';

export const useApiKeys = () => useQuery(apiKeysQueryOptions());

// The key of the create mutation, for the dialog to know a key is on its way.
export const CREATE_API_KEY = ['account', 'api-keys', 'create'];

// Creates a key and adds it to the list, without its secret: that stays with the dialog that
// shows it once. gcTime 0 drops the mutation, and the secret in its answer, from the mutation
// cache as soon as the form that sent it is gone. Refused for the limit (another tab made a key
// meanwhile), the list is asked again.
export function useCreateApiKey() {
  const queryClient = useQueryClient();
  const { queryKey } = apiKeysQueryOptions();
  return useMutation({
    mutationKey: CREATE_API_KEY,
    mutationFn: createApiKey,
    gcTime: 0,
    onSuccess: ({ secret: _secret, ...key }: ApiKeyCreated) =>
      queryClient.setQueryData(queryKey, (keys) => keys && { ...keys, items: [...keys.items, key] }),
    onError: (error) => {
      if (error instanceof ApiError && error.problem === 'too-many-api-keys')
        void queryClient.invalidateQueries({ queryKey });
    },
  });
}

// Revokes a key (Revoke) or removes an expired one (Delete): either way it leaves the list.
export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (key: ApiKey) => revokeApiKey(key.id),
    onSuccess: (_answer, key) =>
      queryClient.setQueryData(
        apiKeysQueryOptions().queryKey,
        (keys) => keys && { ...keys, items: keys.items.filter((item) => item.id !== key.id) },
      ),
  });
}
