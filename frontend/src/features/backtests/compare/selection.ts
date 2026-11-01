import { useSyncExternalStore } from 'react';

// The backtests chosen to compare, up to four, kept in this browser so they stay after a reload.
// A backtest keeps its slot while it is chosen, and its colour with it, so removing another moves
// nothing. The choice belongs to the account that made it: another one signing in starts empty.

export const SLOTS = 4;

// What the Compare panel names a backtest by, without asking the API for it.
export type ComparePick = { id: string; number: number; symbol: string };

type Selection = {
  slots: readonly (ComparePick | null)[];
  // The panel folded to its small button.
  hidden: boolean;
  owner: string | null;
};

const STORAGE_KEY = 'compare-backtests';
const NO_SLOTS: readonly null[] = Array<null>(SLOTS).fill(null);
const EMPTY: Selection = { slots: NO_SLOTS, hidden: false, owner: null };

const isPick = (value: unknown): value is ComparePick => {
  if (typeof value !== 'object' || value === null) return false;
  const { id, number, symbol } = value as Partial<ComparePick>;
  return typeof id === 'string' && id !== '' && typeof number === 'number' && typeof symbol === 'string';
};

// What the browser kept; anything broken, repeated or from another version counts as not chosen.
function read(): Selection {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Partial<Selection> | null;
    if (!Array.isArray(saved?.slots) || saved.slots.length !== SLOTS) return EMPTY;
    const seen = new Set<string>();
    const slots = saved.slots.map((slot: unknown) => {
      if (!isPick(slot) || seen.has(slot.id)) return null;
      seen.add(slot.id);
      return slot;
    });
    return { slots, hidden: saved.hidden === true, owner: typeof saved.owner === 'string' ? saved.owner : null };
  } catch {
    return EMPTY;
  }
}

let selection = read();
const listeners = new Set<() => void>();

const notify = () => {
  listeners.forEach((listener) => {
    listener();
  });
};

function publish(next: Selection) {
  selection = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Without storage the choice lasts until the page closes.
  }
  notify();
}

// Another tab changed the choice, or cleared the storage (no key then).
// (Tests of plain functions run without a window.)
if (typeof window !== 'undefined') {
  window.addEventListener('storage', ({ key }) => {
    if (key !== STORAGE_KEY && key !== null) return;
    selection = read();
    notify();
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function addToCompare(pick: ComparePick) {
  const { slots } = selection;
  const free = slots.indexOf(null);
  if (free < 0 || slots.some((slot) => slot?.id === pick.id)) return;
  publish({ ...selection, slots: slots.map((slot, index) => (index === free ? pick : slot)), hidden: false });
}

export function removeFromCompare(id: string) {
  publish({ ...selection, slots: selection.slots.map((slot) => (slot?.id === id ? null : slot)) });
}

export function clearCompare() {
  publish({ ...EMPTY, owner: selection.owner });
}

export function hideComparePanel(hidden: boolean) {
  publish({ ...selection, hidden });
}

// The signed-in account takes the choice over; another account's is forgotten.
export function claimCompare(owner: string) {
  if (selection.owner === owner) return;
  publish(selection.owner === null ? { ...selection, owner } : { ...EMPTY, owner });
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
