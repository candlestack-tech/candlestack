import { useMutation } from '@tanstack/react-query';
import { useRef, type KeyboardEvent } from 'react';

// Saving a card of Settings as it changes (#216), without a Save button: what each card tells its
// indicator (SaveIndicator.tsx), the saving itself and the keys of a text field.

// Where a card's saving stands: the latest change on its way, saved or failed (Retry sends it
// again), and whether a field holds a value the API would refuse, which is not sent.
export type SaveState = {
  status: 'idle' | 'pending' | 'success' | 'error';
  invalid?: boolean;
  retry: () => void;
};

type Sent<T> = { value: T; seq: number };

type Options<T, R> = {
  // The card's mutations; they go out one after another (their scope).
  key: readonly string[];
  // The saved value: a commit of the same one sends nothing.
  initial: T;
  save: (value: T) => Promise<R>;
  // The API's answer, of the latest change only.
  onSaved: (answer: R) => void;
  same?: (a: T, b: T) => boolean;
};

// Sends a card's value on each commit that changed it. The changes go out in the order they were
// made, never two at once, so the last one wins on the account and in the cache; one that a newer
// one follows in the queue is not sent at all, the newer one carries it. A commit after a failure
// sends again even when the value is the same.
export function useAutosave<T, R>({ key, initial, save, onSaved, same = Object.is }: Options<T, R>) {
  const latest = useRef<Sent<T>>({ value: initial, seq: 0 });
  const mutation = useMutation({
    mutationKey: key,
    scope: { id: key.join('/') },
    mutationFn: async ({ value, seq }: Sent<T>) =>
      seq === latest.current.seq ? { answer: await save(value) } : undefined,
    onSuccess: (sent) => {
      if (sent !== undefined) onSaved(sent.answer);
    },
  });
  const send = (value: T) => {
    latest.current = { value, seq: latest.current.seq + 1 };
    mutation.mutate(latest.current);
  };
  const commit = (value: T) => {
    if (mutation.status === 'error' || !same(value, latest.current.value)) send(value);
  };
  const state: SaveState = { status: mutation.status, retry: () => send(latest.current.value) };
  return { commit, state, error: mutation.error };
}

// A text field saves on Enter (and on leaving it, its onBlur); Esc puts back the saved value.
export function textFieldKeys(commit: () => void, revert: () => void) {
  return {
    onBlur: commit,
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === 'Enter') commit();
      else if (event.key === 'Escape') revert();
    },
  };
}
