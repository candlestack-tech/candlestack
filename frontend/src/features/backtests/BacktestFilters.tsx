import { useNavigate } from '@tanstack/react-router';
import { Globe } from 'lucide-react';
import type { ReactNode } from 'react';

import type { BacktestsPage } from '@/api';
import { FilterSection, FiltersPopover, HOVER_HIGHLIGHT } from '@/components/FiltersPopover';
import type { BacktestsSearch } from '@/routes/backtestsSearch';

import { applyOption, filterOptions, filtersSummary, nextActive, sectionStart, type FilterOption } from './filterRules';
import { ModelName } from './parts';

type Props = { search: BacktestsSearch; page: Pick<BacktestsPage, 'counts' | 'models'> };

// The panel (.fp): 412 px, 12 px inside, its own shadow. It pops in from the button's corner
// (fx-pop, 0.14 s) instead of the popover's own motion and is simply gone on closing; the
// important flag wins over the popover's classes, and Less motion still turns it off.
const PANEL =
  'w-[min(412px,calc(100vw-2rem))] origin-top-left p-3 text-sm/[1.5] shadow-[0_10px_28px_var(--shadow)] data-[state=closed]:animate-none! data-[state=open]:animate-fx-pop! motion-reduce:animate-none!';
// The headings of its sections (.fp-h) keep 8 px under them on a phone too.
const HEADING = 'mb-2';

// A model by its framework's mark, Public only after a globe (public-experiments, bFilters).
function OptionLabel({ option }: { option: FilterOption }) {
  if (option.section === 'model' && option.framework) {
    return <ModelName name={option.label} framework={option.framework} />;
  }
  if (option.section === 'show' && option.show === 'public') {
    return (
      <span className="inline-flex items-center gap-1.5">
        <Globe aria-hidden="true" className="size-4" />
        {option.label}
      </span>
    );
  }
  return option.label;
}

// Show and Model behind one button (mockups/app, btFilters), the panel lined up with the button's
// left edge: Show in its 128 px column, the models beside it with their framework's mark. A pick
// goes to the URL (page 1, a new history entry, so Back undoes it) and leaves the panel open.
export function BacktestFilters({ search, page }: Props) {
  const navigate = useNavigate({ from: '/backtests' });
  const options = filterOptions(search, page);
  const section = (name: FilterOption['section'], render: (option: FilterOption, index: number) => ReactNode) =>
    options.flatMap((option, index) => (option.section === name ? [render(option, index)] : []));
  return (
    <FiltersPopover
      summary={filtersSummary(search)}
      options={options}
      move={(active, key) => nextActive(options, active, key)}
      onPick={(option) => void navigate({ search: (prev) => applyOption(prev, option) })}
      start={() => sectionStart(options, 'show')}
      label={(option) => <OptionLabel option={option} />}
      note={(option) => option.count}
      align="start"
      sideOffset={6}
      openBorder="data-[state=open]:border-[color-mix(in_srgb,var(--accent)_55%,var(--rule))]"
      highlight={HOVER_HIGHLIGHT}
      className={PANEL}
    >
      {(render) => (
        <div className="grid grid-cols-[128px_minmax(0,1fr)] gap-4">
          <FilterSection title="Show" headingClassName={HEADING}>
            {section('show', render)}
          </FilterSection>
          <FilterSection title="Model" headingClassName={HEADING}>
            {section('model', render)}
          </FilterSection>
        </div>
      )}
    </FiltersPopover>
  );
}
