import { useMutation, useMutationState, useQueryClient, type MutationState } from '@tanstack/react-query';
import { useState } from 'react';

import { preferencesQueryOptions, updateAppearance, type Appearance, type Preferences } from '@/api';

const MUTATION_KEY = ['account', 'appearance'];

// Changes some appearance settings: at once on the page (the cache the page reads, which
// AppearanceSync puts on <html>), then on the account. The changes go out one after another, in
// the order they were made; the API's answer is taken only once none is left on the way, so a
// slower answer never undoes a newer choice. A failed change goes back to what was there.
export function useUpdateAppearance() {
  const queryClient = useQueryClient();
  const key = preferencesQueryOptions().queryKey;
  return useMutation({
    mutationKey: MUTATION_KEY,
    scope: { id: 'appearance' },
    mutationFn: (update: Partial<Appearance>) => updateAppearance(update),
    onMutate: async (update): Promise<{ before: Preferences | undefined }> => {
      const before = queryClient.getQueryData(key);
      // A fetch on the way would overwrite the change with the old value. Without anything in the
      // cache yet it is the first one: it carries on, so the account's settings still arrive, and
      // the answer goes on top of them (onSuccess).
      if (before) await queryClient.cancelQueries({ queryKey: key });
      queryClient.setQueryData(
        key,
        (preferences) => preferences && { ...preferences, appearance: { ...preferences.appearance, ...update } },
      );
      return { before };
    },
    onError: (_error, _update, context) => {
      if (context?.before) queryClient.setQueryData(key, context.before);
    },
    onSuccess: async (appearance) => {
      if (queryClient.isMutating({ mutationKey: MUTATION_KEY }) > 1) return;
      // The first fetch may still be on the way, sent before the change: wait for it (a failed one
      // leaves the cache empty, and the next fetch brings the change).
      await queryClient.ensureQueryData(preferencesQueryOptions()).catch(() => undefined);
      queryClient.setQueryData(key, (preferences) => preferences && { ...preferences, appearance });
    },
  });
}

// The latest change, wherever it was made (Settings or the header's toggle): where it stands, why
// it failed, and Retry, which makes it again. Changes that had ended when the caller showed up do
// not count: a failure from before, still in the cache for a while, is not shown again on coming
// back to Settings.
export function useAppearanceSave() {
  const queryClient = useQueryClient();
  const { mutate } = useUpdateAppearance();
  // The cache numbers mutations as they are made.
  const [ended] = useState(() =>
    Math.max(
      0,
      ...queryClient
        .getMutationCache()
        .findAll({ mutationKey: MUTATION_KEY, predicate: (mutation) => mutation.state.status !== 'pending' })
        .map((mutation) => mutation.mutationId),
    ),
  );
  const latest = useMutationState({
    filters: { mutationKey: MUTATION_KEY, predicate: (mutation) => mutation.mutationId > ended },
    select: (mutation) => mutation.state as MutationState<Appearance, Error, Partial<Appearance>>,
  }).at(-1);
  const retry = () => {
    if (latest?.variables) mutate(latest.variables);
  };
  return { status: latest?.status ?? 'idle', error: latest?.error ?? null, retry };
}
