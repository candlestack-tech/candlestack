import type { Direction } from '@/api';
import { ArrowDownIcon, ArrowUpIcon } from '@/components/icons';
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';

// The controls of the Backtest defaults card (mockups/app, ddSettings).

const DIRECTIONS: readonly { value: Direction; title: string; about: string; short: boolean }[] = [
  { value: 'long', title: 'Long only', about: 'Buy on a signal, sell on the exit', short: false },
  { value: 'both', title: 'Long and short', about: 'Also sell short when it points down', short: true },
];

// A slider with its value beside it (.st-sl, .st-val), at most 320 px wide unless `className`
// says otherwise (the builder's take the row).
export function ValueSlider({
  className,
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  className?: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
}) {
  return (
    <div className={cn('flex max-w-[320px] items-center gap-3', className)}>
      <Slider
        className="flex-1"
        aria-label={label}
        aria-valuetext={format(value)}
        min={min}
        max={max}
        step={step}
        value={[value]}
        onValueChange={([next]) => next !== undefined && onChange(next)}
      />
      <span className="min-w-[52px] rounded-md bg-muted px-2 py-[3px] text-center font-mono text-[13px]/[normal] font-semibold">
        {format(value)}
      </span>
    </div>
  );
}

// A number with its unit inside the field, on the surface at the right (.md-in.st-unit), at most
// 260 px wide unless `className` says otherwise (the builder's are as wide as their numbers).
export function UnitInput({
  className,
  label,
  unit,
  value,
  invalid,
  onChange,
}: {
  className?: string;
  label: string;
  unit: string;
  value: string;
  invalid: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <span
      className={cn(
        'flex max-w-[260px] items-center rounded-md border bg-background focus-within:border-[color-mix(in_srgb,var(--accent)_55%,var(--rule))]',
        invalid && 'border-down focus-within:border-down',
        className,
      )}
    >
      <Input
        className="border-0 bg-transparent font-mono focus-visible:ring-0"
        value={value}
        inputMode="decimal"
        aria-label={label}
        aria-invalid={invalid || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      <span className="grid place-items-center self-stretch rounded-r-md border-l bg-muted px-3 font-mono text-[13px] whitespace-nowrap text-muted-foreground">
        {unit}
      </span>
    </span>
  );
}

// The two direction cards: arrows for what it trades, the name and a line on it.
export function DirectionCards({ value, onChange }: { value: Direction; onChange: (value: Direction) => void }) {
  return (
    <ToggleGroup
      value={value}
      onValueChange={onChange}
      aria-label="Direction"
      className="grid max-w-[420px] grid-cols-2 gap-2"
    >
      {DIRECTIONS.map(({ value: direction, title, about, short }) => (
        <ToggleGroupItem
          key={direction}
          value={direction}
          className="flex h-auto flex-col items-start gap-[3px] rounded-lg bg-background px-3 py-2.5 text-left text-sm font-normal text-foreground transition-[border-color,background-color,translate] duration-150 hover:-translate-y-px hover:not-data-[state=on]:border-[color-mix(in_srgb,var(--accent)_45%,var(--rule))] hover:text-foreground data-[state=on]:bg-accent-soft data-[state=on]:text-foreground"
        >
          <span className="mb-0.5 flex gap-0.5">
            <ArrowUpIcon className="size-4 text-up" />
            {short && <ArrowDownIcon className="size-4 text-down" />}
          </span>
          <b>{title}</b>
          <span className="text-[11.5px]/[1.35] text-muted-foreground">{about}</span>
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

// A day of the test period, written as the API writes it (2021-01-04).
export function PeriodInput({
  label,
  value,
  invalid,
  onChange,
}: {
  label: string;
  value: string;
  invalid: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <Input
      className="max-w-[120px] font-mono text-[13px]"
      value={value}
      placeholder="YYYY-MM-DD"
      aria-label={label}
      aria-invalid={invalid || undefined}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
