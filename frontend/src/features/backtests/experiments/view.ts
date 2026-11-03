import { useSyncExternalStore } from 'react';

// Cards or Table on Experiments, kept in this browser like the compare selection, so the page
// opens as it was left. Cards until the user picks Table (public-experiments, S.view).

export type ExperimentsView = 'cards' | 'table';

const STORAGE_KEY = 'experiments-view';

function read(): ExperimentsView {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'table' ? 'table' : 'cards';
  } catch {
    return 'cards';
  }
}

let view = read();
const listeners = new Set<() => void>();

export function setExperimentsView(next: ExperimentsView) {
  view = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Without storage the choice lasts until the page closes.
  }
  listeners.forEach((listener) => {
    listener();
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useExperimentsView(): ExperimentsView {
  return useSyncExternalStore(subscribe, () => view);
}
