import { useSyncExternalStore } from 'react';

import { COMPARE_SLOTS } from '@/routes/compareSearch';

// The backtests chosen to compare, up to four, kept in this browser so they stay after a reload.
// A backtest keeps its slot while it is chosen, and its colour with it, so removing another moves
// nothing. The choice belongs to the account that made it: another one signing in starts empty.

export const SLOTS = COMPARE_SLOTS;

// What the Compare panel names a backtest by, without asking the API for it: its name and
// "AAPL · Kagi · 1h" (backtest-names, .tray-r). A choice kept before backtests had names has
// neither: the panel fills them in from the backtest's document (useNamedPicks) and until then
// names it by its number and instrument. Another account's, added from Experiments, carries its
// author's name; once its author unpublished it, it is `gone` and keeps its slot, saying so, until
// the user dismisses it (public-experiments, .tray-r.gone).
export type ComparePick = {
  id: string;
  number: number;
  name?: string | undefined;
  description?: string | undefined;
  symbol: string;
  author?: string | undefined;
  gone?: true;
};

export const pickName = (pick: ComparePick) => pick.name ?? `Backtest #${String(pick.number)}`;
export const pickDescription = (pick: ComparePick) => pick.description ?? pick.symbol;

const optionalText = (value: unknown) => value === undefined || typeof value === 'string';

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
  const { id, number, name, description, symbol, author, gone } = value as Partial<ComparePick>;
  return (
    typeof id === 'string' &&
    id !== '' &&
    typeof number === 'number' &&
    typeof symbol === 'string' &&
    [name, description, author].every(optionalText) &&
    (gone === undefined || gone === true)
  );
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

// A chosen backtest's name or line changed (a rename, or a choice kept before backtests had
// names): its slot says so, wherever the panel shows.
export function nameInCompare(id: string, name: string, description?: string) {
  const { slots } = selection;
  const slot = slots.find((each) => each?.id === id);
  if (!slot) return;
  const named = { ...slot, name, description: description ?? slot.description };
  if (named.name === slot.name && named.description === slot.description) return;
  publish({ ...selection, slots: slots.map((each) => (each?.id === id ? named : each)) });
}

// A link to the compare page (?ids=) chooses its backtests, in its order, in place of the choice.
// One chosen already keeps what the panel knew of it, and one unpublished since is asked again;
// the comparison names a new one (useComparedNames) and leaves out one that is gone. A link that
// names the backtests compared already, in any order, keeps their slots and colours.
export function openInCompare(ids: readonly string[]) {
  const known = new Map(selection.slots.flatMap((slot) => (slot ? [[slot.id, slot] as const] : [])));
  const compared = [...known.values()].every((slot) => !slot.gone);
  if (compared && known.size === ids.length && ids.every((id) => known.has(id))) return;
  const slots = NO_SLOTS.map((_, index) => {
    const id = ids[index];
    if (id === undefined) return null;
    const { gone: _gone, ...pick } = known.get(id) ?? { id, number: 0, symbol: '' };
    return pick;
  });
  if (JSON.stringify(slots) !== JSON.stringify(selection.slots)) publish({ ...selection, slots });
}

// What the comparison tells of a backtest a link chose (openInCompare): its number, and its
// author's name when it is another account's, so it counts as from Experiments.
export function creditInCompare(id: string, number: number, author: string | undefined) {
  const { slots } = selection;
  const slot = slots.find((each) => each?.id === id);
  if (!slot || (slot.number === number && slot.author === author)) return;
  publish({ ...selection, slots: slots.map((each) => (each?.id === id ? { ...each, number, author } : each)) });
}

export function removeFromCompare(id: string) {
  publish({ ...selection, slots: selection.slots.map((slot) => (slot?.id === id ? null : slot)) });
}

// A chosen backtest whose page answers 404. The user's own was deleted and leaves the choice;
// another account's was unpublished by its author: it leaves the comparison but keeps its slot,
// saying so, until the user dismisses it.
export function leaveCompare(id: string) {
  const { slots } = selection;
  const slot = slots.find((each) => each?.id === id);
  if (!slot || slot.gone) return;
  if (slot.author === undefined) removeFromCompare(id);
  else publish({ ...selection, slots: slots.map((each) => (each?.id === id ? { ...each, gone: true } : each)) });
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

// The IDs of the backtests compared, in the order of their slots, as the compare page's URL names
// them: `bt_12,bt_11`, or '' with none.
export const comparedIds = (picks: readonly ComparePick[]) => picks.map((pick) => pick.id).join(',');

// The tint of a row whose backtest is in the comparison.
export function useComparedRow(id: string) {
  const { picks } = useCompareSelection();
  return picks.some((pick) => pick.id === id) ? '[&>td]:bg-accent-soft' : undefined;
}

// The backtests compared (`picks`) and those that were unpublished (`gone`); a gone one still
// takes its slot, so the choice is full when no slot is free.
export function useCompareSelection() {
  const { slots, hidden } = useSyncExternalStore(subscribe, () => selection);
  const picks = slots.filter((slot): slot is ComparePick => slot !== null && !slot.gone);
  const gone = slots.filter((slot): slot is ComparePick => slot?.gone === true);
  return { slots, picks, gone, hidden, full: !slots.includes(null) };
}

// What the compare page's back link calls the page it was opened from; a backtest's page by the
// backtest's name once it is known (backtest-names).
export function comparedFromOf(pathname: string, backtestName?: string): string {
  if (pathname === '/dashboard') return 'Dashboard';
  if (pathname === '/backtests') return 'Backtests';
  if (pathname === '/experiments') return 'Experiments';
  if (pathname === '/models') return 'Models';
  if (pathname.startsWith('/models/')) return 'Model';
  if (pathname === '/backtests/new') return 'Back';
  return pathname.startsWith('/backtests/') ? (backtestName ?? 'Backtest') : 'Back';
}
