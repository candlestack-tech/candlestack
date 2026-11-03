import type { BacktestsPage } from '@/api';
import { moveInList, typeAhead } from '@/lib/listKeys';
import type { BacktestsSearch } from '@/routes/backtestsSearch';

// The rules of the Backtests page's Filters panel (mockups/app, btFilters): Show (all, starred,
// public or failed; public-experiments, B_SHOW) and Model, both in the URL, applied together.

type Show = BacktestsSearch['show'];

const SHOWS: readonly { show: Show; label: string; count: keyof BacktestsPage['counts'] }[] = [
  { show: undefined, label: 'All backtests', count: 'all' },
  { show: 'starred', label: 'Starred', count: 'starred' },
  { show: 'public', label: 'Public only', count: 'public' },
  { show: 'failed', label: 'Failed', count: 'failed' },
];

type ShowOption = { section: 'show'; show: Show; label: string; count: number; selected: boolean };
type ModelOption = {
  section: 'model';
  model: string | undefined;
  framework: string | undefined;
  label: string;
  count: number | undefined;
  selected: boolean;
};
export type FilterOption = ShowOption | ModelOption;

const sameModel = (a: string | undefined, b: string | undefined) => a?.toLowerCase() === b?.toLowerCase();

// Show's three entries with the totals of the account, then All models and every model the
// backtests used, with how many used it.
export function filterOptions(search: BacktestsSearch, page: Pick<BacktestsPage, 'counts' | 'models'>): FilterOption[] {
  const shows = SHOWS.map(({ show, label, count }): FilterOption => ({
    section: 'show',
    show,
    label,
    count: page.counts[count],
    selected: show === search.show,
  }));
  const models = page.models.map((model): FilterOption => ({
    section: 'model',
    model: model.name,
    framework: model.framework,
    label: model.name,
    count: model.count,
    selected: sameModel(model.name, search.model),
  }));
  const all: FilterOption = {
    section: 'model',
    model: undefined,
    framework: undefined,
    label: 'All models',
    count: undefined,
    selected: search.model === undefined,
  };
  return [...shows, all, ...models];
}

// The Filters button's text: "All backtests", "Starred", "Failed · stockmixer.onnx",
// "blesk-novol-8.keras".
export function filtersSummary(search: BacktestsSearch): string {
  const show = search.show && SHOWS.find((each) => each.show === search.show)?.label;
  const parts = [show, search.model].filter(Boolean);
  return parts.length === 0 ? 'All backtests' : parts.join(' · ');
}

// The URL after picking an entry: like every change, it starts at page 1.
export function applyOption(search: BacktestsSearch, option: FilterOption): BacktestsSearch {
  if (option.section === 'show') return { ...search, show: option.show, page: undefined };
  return { ...search, model: option.model, page: undefined };
}

// Where the focus goes in a section: its chosen entry, or its first.
export function sectionStart(options: readonly FilterOption[], section: FilterOption['section']): number {
  const chosen = options.findIndex((option) => option.section === section && option.selected);
  return chosen >= 0 ? chosen : options.findIndex((option) => option.section === section);
}

// Show on the left, Model on the right: ArrowLeft and ArrowRight go to the other section, the
// other arrows, Home and End move in the section; a letter goes to the next entry that starts
// with it anywhere in the panel.
export function nextActive(options: readonly FilterOption[], active: number, key: string): number | undefined {
  const section = options[active]?.section ?? 'show';
  if (key === 'ArrowLeft') return section === 'show' ? active : sectionStart(options, 'show');
  if (key === 'ArrowRight') return section === 'model' ? active : sectionStart(options, 'model');
  const inSection = options.flatMap((option, index) => (option.section === section ? [index] : []));
  const moved = moveInList(inSection, Math.max(0, inSection.indexOf(active)), key);
  if (moved !== undefined) return moved;
  const all = options.map((_, index) => index);
  return typeAhead(
    options.map((option) => option.label),
    all,
    active,
    key,
  );
}
