import { useSyncExternalStore } from 'react';

// The backtests chosen to compare, up to four, kept in this browser so they stay after a reload.
// A backtest keeps its slot while it is chosen, and its colour with it, so removing another moves
// nothing.

export const SLOTS = 4;

// What the Compare panel names a backtest by, without asking the API for it.
export type ComparePick = { id: string; number: number; symbol: string };

type Selection = {
  slots: readonly (ComparePick | null)[];
  // The panel folded to its small button.
  hidden: boolean;
};

const STORAGE_KEY = 'compare-backtests';
const EMPTY: Selection = { slots: Array<null>(SLOTS).fill(null), hidden: false };

const isPick = (value: unknown): value is ComparePick => {
  if (typeof value !== 'object' || value === null) return false;
  const { id, number, symbol } = value as Partial<ComparePick>;
  return typeof id === 'string' && typeof number === 'number' && typeof symbol === 'string';
};

// What the browser kept; anything broken or from another version counts as nothing chosen.
function read(): Selection {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Partial<Selection> | null;
    if (!Array.isArray(saved?.slots) || saved.slots.length !== SLOTS) return EMPTY;
    const slots = saved.slots.map((slot: unknown) => (isPick(slot) ? slot : null));
    return { slots, hidden: saved.hidden === true };
  } catch {
    return EMPTY;
  }
}

let selection = read();
const listeners = new Set<() => void>();

function publish(next: Selection) {
  selection = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Without storage the choice lasts until the page closes.
  }
  listeners.forEach((listener) => {
    listener();
  });
}

function subscribe(listener: () => void) {
  // Another tab changed the choice.
  const onStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY) return;
    selection = read();
    listener();
  };
  listeners.add(listener);
  window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('storage', onStorage);
  };
}

export function addToCompare(pick: ComparePick) {
  const { slots } = selection;
  const free = slots.indexOf(null);
  if (free < 0 || slots.some((slot) => slot?.id === pick.id)) return;
  publish({ slots: slots.map((slot, index) => (index === free ? pick : slot)), hidden: false });
}

export function removeFromCompare(id: string) {
  publish({ ...selection, slots: selection.slots.map((slot) => (slot?.id === id ? null : slot)) });
}

export function clearCompare() {
  publish(EMPTY);
}

export function hideComparePanel(hidden: boolean) {
  publish({ ...selection, hidden });
}

// The tint of a row whose backtest is in the comparison.
export function useComparedRow(id: string) {
  const { picks } = useCompareSelection();
  return picks.some((pick) => pick.id === id) ? '[&>td]:bg-accent-soft' : undefined;
}

export function useCompareSelection() {
  const { slots, hidden } = useSyncExternalStore(subscribe, () => selection);
  const picks = slots.filter((slot) => slot !== null);
  return { slots, picks, hidden, full: picks.length >= SLOTS };
}
