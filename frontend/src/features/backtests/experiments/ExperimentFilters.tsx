import { useNavigate } from '@tanstack/react-router';
import { ArrowDownUp, Check, ChevronDown, X } from 'lucide-react';
import type { ReactNode } from 'react';

import type { ExperimentsPage } from '@/api';
import { DROPDOWN_CHEVRON } from '@/components/buttonStyles';
import { InstrumentIcon } from '@/components/InstrumentIcon';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';
import { withSortPick, type ExperimentsSearch } from '@/routes/experimentsSearch';

import { AuthorAvatar } from './AuthorAvatar';
import {
  SORT_OPTIONS,
  marketOptions,
  marketValue,
  representationOptions,
  representationValue,
  sortValue,
  timeframeOptions,
  type ChipOption,
} from './chipRules';

// The chips of Experiments (public-experiments, .fchip, .dp): 34 px with a 6 px radius like the
// Filters button, muted, ink on hover and while open; a set one has the accent's rim and says its
// value in ink. Each opens a 236 px list of one choice with its count, the chosen one checked.

const SET_RIM = 'border-[color-mix(in_srgb,var(--accent)_55%,var(--rule))]';
const CHIP =
  'group inline-flex h-[34px] flex-none cursor-pointer items-center gap-1.5 rounded-md border bg-background pr-[9px] pl-3 text-sm whitespace-nowrap text-muted-foreground transition-colors duration-150 outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=open]:border-[color-mix(in_srgb,var(--accent)_55%,var(--rule))] data-[state=open]:text-foreground motion-reduce:transition-none';
const ITEM = 'h-8 px-2 text-sm whitespace-nowrap data-[state=checked]:font-medium';

type Navigate = (change: (search: ExperimentsSearch) => ExperimentsSearch) => void;

function useApply(): Navigate {
  const navigate = useNavigate({ from: '/experiments' });
  return (change) => void navigate({ search: (prev) => change(prev) });
}

type ChipProps = {
  label: ReactNode;
  value: string | undefined;
  align?: 'start' | 'end';
  icon?: ReactNode;
  children: ReactNode;
};

function Chip({ label, value, align = 'start', icon, children }: ChipProps) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger className={cn(CHIP, value !== undefined && SET_RIM)}>
        {icon}
        {label}
        {value !== undefined && (
          <>
            {' '}
            <b className="font-medium text-foreground">{value}</b>
          </>
        )}
        <ChevronDown aria-hidden="true" className={DROPDOWN_CHEVRON} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} sideOffset={6} className="w-[236px] p-1.5">
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// An entry: its name (an instrument with its icon), its count or note, and the check.
function Entry({ option, note }: { option: Pick<ChipOption, 'key' | 'label' | 'icon' | 'count'>; note?: string }) {
  return (
    <DropdownMenuRadioItem
      value={option.key}
      className={cn(ITEM, note === undefined && option.count === 0 && 'text-muted-foreground')}
    >
      <span className="inline-flex min-w-0 items-center gap-1.5 truncate">
        {option.icon && <InstrumentIcon id={option.icon} size={16} />}
        <span className={cn(option.icon && 'font-mono')}>{option.label}</span>
      </span>
      <span className={cn('ml-auto text-xs text-muted-foreground', note === undefined && 'font-mono')}>
        {note ?? option.count}
      </span>
      <Check
        aria-hidden="true"
        className="invisible size-4 flex-none text-primary group-data-[state=checked]/item:visible"
      />
    </DropdownMenuRadioItem>
  );
}

function Choices({ options, apply }: { options: readonly ChipOption[]; apply: Navigate }) {
  const chosen = options.find((option) => option.selected)?.key ?? '';
  return (
    <DropdownMenuRadioGroup
      value={chosen}
      onValueChange={(key) => {
        const option = options.find((each) => each.key === key);
        if (option) apply(option.apply);
      }}
    >
      {options.map((option) => (
        <Entry key={option.key} option={option} />
      ))}
    </DropdownMenuRadioGroup>
  );
}

type Props = { search: ExperimentsSearch; facets: ExperimentsPage['facets'] };

// Market: All markets, Stocks, Crypto, then the instruments.
function MarketChip({ search, facets }: Props) {
  const apply = useApply();
  const { markets, instruments } = marketOptions(search, facets);
  const all = [...markets, ...instruments];
  return (
    <Chip label="Market" value={marketValue(search)}>
      <DropdownMenuRadioGroup
        value={all.find((option) => option.selected)?.key ?? ''}
        onValueChange={(key) => {
          const option = all.find((each) => each.key === key);
          if (option) apply(option.apply);
        }}
      >
        {markets.map((option) => (
          <Entry key={option.key} option={option} />
        ))}
        {instruments.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="px-2 pt-1.5 pb-1 text-xs font-medium text-muted-foreground">
              Instrument
            </DropdownMenuLabel>
            {instruments.map((option) => (
              <Entry key={option.key} option={option} />
            ))}
          </>
        )}
      </DropdownMenuRadioGroup>
    </Chip>
  );
}

function RepresentationChip({ search, facets }: Props) {
  const apply = useApply();
  return (
    <Chip label="Representation" value={representationValue(search)}>
      <Choices options={representationOptions(search, facets)} apply={apply} />
    </Chip>
  );
}

function TimeframeChip({ search, facets }: Props) {
  const apply = useApply();
  return (
    <Chip label="Timeframe" value={search.timeframe}>
      <Choices options={timeframeOptions(search, facets)} apply={apply} />
    </Chip>
  );
}

// Sort, at the right of the row; its list lines up with the chip's right edge. A pick sorts from
// the top; the second way comes from the table's headers.
export function SortChip({ search }: { search: ExperimentsSearch }) {
  const apply = useApply();
  const chosen = SORT_OPTIONS.find((option) => option.column === search.sort) ?? SORT_OPTIONS.at(-1);
  return (
    <Chip
      label="Sort"
      value={sortValue(search)}
      align="end"
      icon={<ArrowDownUp aria-hidden="true" className="size-[15px]" />}
    >
      <DropdownMenuRadioGroup
        value={chosen?.label ?? 'Newest'}
        onValueChange={(label) => {
          const option = SORT_OPTIONS.find((each) => each.label === label);
          if (option) apply((prev) => withSortPick(prev, option.column));
        }}
      >
        {SORT_OPTIONS.map((option) => {
          const up = option === chosen && search.order === 'asc';
          return (
            <Entry
              key={option.label}
              option={{ key: option.label, label: option.label, count: 0 }}
              note={up ? option.up : option.down}
            />
          );
        })}
      </DropdownMenuRadioGroup>
    </Chip>
  );
}

// The author of "More by Alan": the initials and the name, and the cross that shows everyone.
function AuthorChip({ name }: { name: string }) {
  const apply = useApply();
  return (
    <button
      type="button"
      aria-label="Remove the author filter"
      title="Show everyone"
      onClick={() => {
        apply((prev) => ({ ...prev, author: undefined, page: undefined }));
      }}
      className={cn(CHIP, SET_RIM, 'group/who gap-[7px] pl-1.5')}
    >
      <AuthorAvatar name={name} size="xs" />
      <b className="font-medium text-foreground">{name}</b>
      <span className="-mr-[3px] grid size-5 place-items-center rounded-[4px] text-muted-foreground group-hover/who:bg-surface-2 group-hover/who:text-foreground">
        <X aria-hidden="true" className="size-[13px]" />
      </span>
    </button>
  );
}

// The author chip once an author is chosen, then Market, Representation and Timeframe.
export function ExperimentChips({ search, facets, author }: Props & { author: string | undefined }) {
  return (
    <>
      {search.author && <AuthorChip name={author ?? 'One author'} />}
      <MarketChip search={search} facets={facets} />
      <RepresentationChip search={search} facets={facets} />
      <TimeframeChip search={search} facets={facets} />
    </>
  );
}
