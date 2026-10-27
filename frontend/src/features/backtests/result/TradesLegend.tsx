import type { ReactNode } from 'react';

import type { Bar } from '@/features/instrument';
import { formatDate, formatShortDay, formatTime, type DateStyle } from '@/lib/dates';
import { formatCompact, formatPrice, formatPriceWith, formatSignedChange } from '@/lib/numbers';
import { formatCount, formatPercent, returnTone } from '@/lib/results';
import { cn } from '@/lib/utils';

import { DENSE_MOST, type TradeRow } from './chartData';
import { TONE } from './format';

// The legends over a backtest's chart, as TradingView has them (#309): at the top left the
// candle under the cursor (the last one off the chart) and the trades, at the top of the
// strategy's pane its return against buy and hold. They let the cursor through to the chart,
// the buttons to hide a line aside.

const MUTED = 'text-muted-foreground';

function Value({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <span>
      <span className={MUTED}>{label}</span> <span className={tone}>{value}</span>
    </span>
  );
}

// "O 334.20 H 335.10 L 333.80 C 334.90 +1.10 (+0.33%)" in the colour of the candle, the change
// against the candle before.
function Prices({ bar, before, decimals }: { bar: Bar; before: Bar | undefined; decimals: number }) {
  const tone = bar.close >= bar.open ? 'text-candle-up' : 'text-candle-down';
  const price = (value: number) => formatPriceWith(value, decimals);
  const base = before?.close ?? bar.open;
  const change = bar.close - base;
  const sign = change < 0 ? '−' : '+';
  return (
    <>
      <Value label="O" value={price(bar.open)} tone={tone} />
      <Value label="H" value={price(bar.high)} tone={tone} />
      <Value label="L" value={price(bar.low)} tone={tone} />
      <Value label="C" value={price(bar.close)} tone={tone} />
      <span className={tone}>
        {sign}
        {price(Math.abs(change))} ({formatSignedChange(base === 0 ? 0 : (change / base) * 100)})
      </span>
    </>
  );
}

// A trade's time as the table writes it, in one line: "3 Jun 10:30", a run of days "3 Jun 2024".
function tradeTime(seconds: number, style: DateStyle, daily: boolean): string {
  const date = new Date(seconds * 1000);
  return daily ? formatDate(date, style) : `${formatShortDay(date, style)} ${formatTime(date, style)}`;
}

// The trade under the cursor: its side, when and at what it opened and closed, its return, how
// many candles and why it closed.
function TradeLine({ trade, style, daily }: { trade: TradeRow; style: DateStyle; daily: boolean }) {
  const when = (seconds: number) => tradeTime(seconds, style, daily);
  return (
    <span className="flex flex-wrap gap-x-2.5 rounded bg-background/85 px-1.5">
      <span className={trade.long ? 'text-primary' : 'text-down'}>{trade.long ? '▲ Long' : '▼ Short'}</span>
      <span>
        {when(trade.openTs)} → {when(trade.closeTs)}
      </span>
      <span className="font-mono">
        {formatPrice(trade.openPrice)} → {formatPrice(trade.closePrice)}
      </span>
      <span className={cn('font-mono', TONE[returnTone(trade.return)])}>{formatPercent(trade.return)}</span>
      <span className={MUTED}>
        {formatCount(trade.candles)} {trade.candles === 1 ? 'candle' : 'candles'} · {trade.reason}
      </span>
    </span>
  );
}

// What the arrows and the shading are, and how many trades are in view.
function Key({ inView }: { inView: number }) {
  return (
    <span className={cn('flex flex-wrap gap-x-2.5', MUTED)}>
      <span>
        <span className="text-primary">▲</span> Buy
      </span>
      <span>
        <span className="text-down">▼</span> Sell
      </span>
      <span>
        <i aria-hidden="true" className="inline-block size-2.5 rounded-xs bg-primary/25 align-[-1px]" /> In a position
      </span>
      <span>
        · {formatCount(inView)} {inView === 1 ? 'trade' : 'trades'} in view
        {inView > DENSE_MOST && ', zoom in to see each one'}
      </span>
    </span>
  );
}

type PriceProps = {
  title: string;
  bar: Bar | undefined;
  before: Bar | undefined;
  decimals: number;
  trades: ReactNode;
};

const ROW = 'flex flex-wrap items-center gap-x-2.5';

export function PriceLegend({ title, bar, before, decimals, trades }: PriceProps) {
  return (
    <div className="pointer-events-none absolute top-2 left-3 z-10 flex max-w-[calc(100%-6rem)] flex-col text-xs/5">
      <div className={ROW}>
        <span className="font-sans text-[13px] font-medium">{title}</span>
        {bar && (
          <span className={cn(ROW, 'font-mono')}>
            <Prices bar={bar} before={before} decimals={decimals} />
          </span>
        )}
      </div>
      {bar && (
        <div className="font-mono">
          <Value
            label="Vol"
            value={formatCompact(bar.volume)}
            tone={bar.close >= bar.open ? 'text-candle-up' : 'text-candle-down'}
          />
        </div>
      )}
      {trades}
    </div>
  );
}

// The third line: the trade under the cursor, else the key.
export function TradesNote({
  trade,
  inView,
  style,
  daily,
}: {
  trade: TradeRow | null;
  inView: number;
  style: DateStyle;
  daily: boolean;
}) {
  return trade ? <TradeLine trade={trade} style={style} daily={daily} /> : <Key inView={inView} />;
}

type Line = { value: number | undefined; on: boolean; onToggle: () => void };

function LineToggle({ label, swatch, tone, line }: { label: string; swatch: string; tone: string; line: Line }) {
  return (
    <button
      type="button"
      aria-pressed={line.on}
      // The name stays the line's: the value under it changes with the cursor.
      aria-label={label}
      title="Show or hide"
      onClick={line.onToggle}
      className={cn(
        'pointer-events-auto -ml-1 inline-flex items-center gap-1.5 rounded px-1 outline-none select-none hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50',
        !line.on && 'opacity-40',
      )}
    >
      <i aria-hidden="true" className={cn('inline-block w-3', swatch)} />
      {label}
      <span className={cn('font-mono', tone, !line.on && 'invisible')}>
        {line.value === undefined ? '' : formatPercent(line.value)}
      </span>
    </button>
  );
}

type ReturnsProps = { top: number; strategy: Line; benchmark: Line | null; drawdown: number | undefined };

// Under the price: the strategy's return and buy and hold's at the candle, each a button that
// hides its line, then the drawdown.
export function ReturnsLegend({ top, strategy, benchmark, drawdown }: ReturnsProps) {
  return (
    <div
      style={{ top }}
      className="pointer-events-none absolute left-3 z-10 flex flex-wrap items-center gap-x-2.5 text-xs/5"
    >
      <LineToggle label="Strategy" swatch="h-0.5 rounded-xs bg-primary" tone="text-primary" line={strategy} />
      {benchmark && (
        <LineToggle
          label="Buy and hold"
          swatch="h-0 border-t-2 border-dashed border-muted-foreground"
          tone={MUTED}
          line={benchmark}
        />
      )}
      <span>
        <span className={cn('font-mono', MUTED)}>Drawdown</span>{' '}
        <span className="font-mono text-down">{drawdown === undefined ? '' : formatPercent(drawdown)}</span>
      </span>
    </div>
  );
}
