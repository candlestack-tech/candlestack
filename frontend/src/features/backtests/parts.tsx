import { Globe } from 'lucide-react';
import type { ReactNode } from 'react';

import type { Backtest, BacktestStatus } from '@/api';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';
import { FrameworkMini, frameworkOf } from '@/features/models';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentTime } from '@/lib/dates';
import { formatPercent, MISSING } from '@/lib/results';
import { cn } from '@/lib/utils';

import {
  aheadOf,
  aheadText,
  hasResults,
  instrumentId,
  instrumentList,
  instrumentsShown,
  returnClass,
  symbolsOf,
} from './format';
import { NameTooltip } from './NameTooltip';
import { useNameTooltip } from './useNameTooltip';

// The small parts every list of backtests draws the same way (mockups/app).

// The instruments with their icons (InstrumentSymbol, #318): up to `max`, or `max - 1` and "+N"
// for the rest, all of them in the title then. On one line: where the column is narrower than
// them, the symbols give way with an ellipsis. `size` 16 is the slimmer row of the dashboard's
// starred backtests.
export function BacktestInstruments({
  backtest,
  max,
  size = 20,
}: {
  backtest: Pick<Backtest, 'instruments'>;
  max: number;
  size?: 16 | 20;
}) {
  const { shown, more } = instrumentsShown(backtest.instruments, max);
  return (
    <span
      title={more > 0 ? instrumentList(symbolsOf(backtest)) : undefined}
      className="flex max-w-full min-w-0 items-center gap-2"
    >
      {shown.map((instrument) => (
        <InstrumentSymbol
          key={instrumentId(instrument)}
          id={instrumentId(instrument)}
          symbol={instrument.symbol}
          size={size}
        />
      ))}
      {more > 0 && <span className="shrink-0 font-mono text-muted-foreground">+{more}</span>}
    </span>
  );
}

// "#12", the number of the backtest in its author's account: small and muted after its name, in
// tables only (backtest-names, .bid).
export function BacktestNumber({ backtest, className }: { backtest: Pick<Backtest, 'number'>; className?: string }) {
  return (
    <span className={cn('flex-none font-mono text-[11.5px] whitespace-nowrap text-muted-foreground', className)}>
      #{backtest.number}
    </span>
  );
}

type NameProps = { name: string; className?: string };

// A backtest's name on one line, cut with an ellipsis where it has no room, the full name then in
// a tooltip on hover or focus (backtest-names, .bn). It sits above the link a row stretches over
// itself, so hovering it shows the tooltip; a click still opens the row.
export function BacktestName({ name, className }: NameProps) {
  const { ref, at } = useNameTooltip<HTMLSpanElement>();
  return (
    <>
      <span ref={ref} className={cn('relative z-[1] block min-w-0 truncate', className)}>
        {name}
      </span>
      {at && <NameTooltip text={name} at={at} />}
    </>
  );
}

// The title of a backtest's page: up to two lines, broken anywhere, then an ellipsis and the same
// tooltip (backtest-names, .r7-t h1).
export function BacktestTitle({ name, className }: NameProps) {
  const { ref, at } = useNameTooltip<HTMLHeadingElement>();
  return (
    <>
      <h1 ref={ref} className={cn('line-clamp-2 min-w-0 [overflow-wrap:anywhere]', className)}>
        {name}
      </h1>
      {at && <NameTooltip text={name} at={at} />}
    </>
  );
}

// In a table, one line: the name, then the number 6 px after it (backtest-names, .bcell). The
// name stops at `max` (150 px in Backtests and on the Dashboard, 160 px in Experiments) or where
// the cell ends.
export function BacktestNameNumber({
  backtest,
  max = 'max-w-[150px]',
}: {
  backtest: Pick<Backtest, 'name' | 'number'>;
  max?: string;
}) {
  return (
    <span className="flex max-w-full min-w-0 items-center gap-1.5">
      <BacktestName name={backtest.name} className={cn('font-medium text-foreground', max)} />
      <BacktestNumber backtest={backtest} />
    </span>
  );
}

// Two lines where nothing else describes the backtest: the name, then what it ran on, what its
// model reads and the timeframe in muted mono, "AAPL · Kagi · 1h" (backtest-names, .bn, .bsub).
// Each place sizes the name; `lead` comes before the name on its line (an author's initials), and
// `wrap` puts the name in what it is there (the link to the backtest, the card's heading).
export function BacktestNameLines({
  name,
  description,
  nameClassName,
  descriptionClassName,
  lead,
  wrap,
}: {
  name: string;
  description: string;
  nameClassName?: string;
  descriptionClassName?: string;
  lead?: ReactNode;
  wrap?: (name: ReactNode) => ReactNode;
}) {
  const shown = <BacktestName name={name} className={nameClassName} />;
  const title = wrap ? wrap(shown) : shown;
  return (
    <span className="flex min-w-0 flex-1 flex-col">
      {lead ? (
        <span className="flex min-w-0 items-center gap-[5px]">
          {lead}
          {title}
        </span>
      ) : (
        title
      )}
      <span className={cn('block truncate font-mono text-[11.5px]/4 text-muted-foreground', descriptionClassName)}>
        {description}
      </span>
    </span>
  );
}

const BADGES: Record<BacktestStatus, { text: string; tone: string }> = {
  queued: { text: 'Queued', tone: 'text-muted-foreground border-border' },
  finished: { text: 'Finished', tone: 'text-up border-[color-mix(in_srgb,var(--up)_45%,var(--rule))]' },
  running: { text: 'Running', tone: 'text-primary border-[color-mix(in_srgb,var(--accent)_45%,var(--rule))]' },
  failed: { text: 'Failed', tone: 'text-down border-[color-mix(in_srgb,var(--down)_45%,var(--rule))]' },
};

// How far a backtest got (.badge.ok, .badge.run, .badge.bad): Queued in grey, Finished in green,
// Running in the accent, Failed in red; `detail` after a dot ("Running · 1 min 5 s", "Queued · 2
// ahead"), `title` on hover and `spoken` after it for screen readers only.
export function StatusBadge({
  status,
  detail,
  title,
  spoken,
}: {
  status: BacktestStatus;
  detail?: string | undefined;
  title?: string | undefined;
  spoken?: string | undefined;
}) {
  const { text, tone } = BADGES[status];
  return (
    <span
      title={title}
      className={cn(
        'inline-block rounded-[4px] border px-1.5 py-px font-mono text-[11px]/4 font-medium whitespace-nowrap',
        tone,
      )}
    >
      {detail ? `${text} · ${detail}` : text}
      {spoken && <span className="sr-only">{spoken}</span>}
    </span>
  );
}

// The 14 px globe after the number of a backtest the user published (public-experiments, .glb),
// named for screen readers as FrameworkMini is.
const PUBLIC = { role: 'img', 'aria-label': 'Public, in Experiments' };

export function PublicMark() {
  return (
    <span title="Public, in Experiments" className="inline-grid flex-none place-items-center text-primary">
      <Globe {...PUBLIC} className="size-3.5" />
    </span>
  );
}

// A missing result: a muted en dash.
export function Missing() {
  return <span className="text-muted-foreground">{MISSING}</span>;
}

// The return in bold, green or red; the only result with a colour.
export function ReturnValue({ value }: { value: number }) {
  return <b className={cn('font-semibold', returnClass(value))}>{formatPercent(value)}</b>;
}

// The model by its framework's 14 px mark (.bt-m), the name beside it. Where it has less room than
// it needs (an entry of the Filters panel on a phone), the name ends in an ellipsis; with `wrap`
// (the Backtests table, where long file names are common) it takes up to two lines, broken
// anywhere, and only then ends in one.
export function ModelName({ name, framework, wrap = false }: { name: string; framework: string; wrap?: boolean }) {
  const mark = frameworkOf(framework);
  return (
    <span className={cn('inline-flex max-w-full gap-[7px]', wrap ? 'items-start' : 'items-center')}>
      {mark && <FrameworkMini framework={mark} />}
      <span className={cn('min-w-0', wrap ? 'line-clamp-2 whitespace-normal [overflow-wrap:anywhere]' : 'truncate')}>
        {name}
      </span>
    </span>
  );
}

// The badge of a backtest in a list: a queued one with its place in the queue, "Queued · 2" (the
// Return column has no room for more), what it means in the title and, for screen readers, " ahead"
// after it.
function ListBadge({ backtest }: { backtest: Backtest }) {
  const ahead = aheadOf(backtest);
  if (ahead === null) return <StatusBadge status={backtest.status} />;
  return <StatusBadge status="queued" detail={String(ahead)} title={aheadText(ahead)} spoken=" ahead" />;
}

// A backtest's return, or its badge (Queued, Running, Failed) when it has none.
export function BacktestReturn({ backtest }: { backtest: Backtest }) {
  const value = hasResults(backtest) ? backtest.total_return : null;
  return value === null ? <ListBadge backtest={backtest} /> : <ReturnValue value={value} />;
}

// When it ran, on the viewer's clock in the user's formats: "Today 16:05".
export function BacktestWhen({ backtest }: { backtest: Backtest }) {
  const dateStyle = useViewerDateStyle();
  return formatRecentTime(new Date(backtest.created_at), dateStyle);
}
