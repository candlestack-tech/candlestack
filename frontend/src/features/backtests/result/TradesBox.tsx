import { useState } from 'react';

import { BUTTON_SIZES, GHOST_BUTTON } from '@/components/buttonStyles';
import { HINT } from '@/components/Hint';
import { Button } from '@/components/ui/button';
import { useDateStyle } from '@/features/preferences';
import { formatDate, formatTime, type DateStyle } from '@/lib/dates';
import { formatPrice } from '@/lib/numbers';
import { formatCount, formatPercent, returnTone } from '@/lib/results';
import { cn } from '@/lib/utils';

import { tradeRows, type TradeRow, type Trades } from './chartData';
import { TONE } from './format';
import { ResultBox } from './ResultParts';

// The newest trades the page shows before Show all.
export const FIRST_TRADES = 6;

const HEAD = 'border-b px-3 py-1.5 text-left font-medium whitespace-nowrap text-muted-foreground';
const CELL = 'border-b px-3 py-1.5 whitespace-nowrap tabular-nums [tr:last-child>&]:border-b-0';
const NUMBER = 'text-right font-mono';
const TIME = 'font-mono text-xs';
// What a narrow box leaves out, so the rest fits: the candles and why it closed below 720 px, the
// prices below 560 px (the table's own width, not the screen's).
const WIDE = '@max-[720px]:hidden';
const PRICE = '@max-[560px]:hidden';

type Columns = { opened: string; closed: string; side: boolean };

// A long-only run buys and sells, as in the mockup; one that also goes short opens and closes, and
// says which way.
const columnsOf = (longOnly: boolean): Columns =>
  longOnly ? { opened: 'Bought', closed: 'Sold', side: false } : { opened: 'Opened', closed: 'Closed', side: true };

// A candle's time: its day, and under it its time of day for candles shorter than a day.
function CandleTime({ seconds, style, daily }: { seconds: number; style: DateStyle; daily: boolean }) {
  const date = new Date(seconds * 1000);
  if (daily) return formatDate(date, style);
  return (
    <>
      {formatDate(date, style)}
      <span className="block text-muted-foreground">{formatTime(date, style)}</span>
    </>
  );
}

// The chart and the table point at the same trade: a row of the trade under the chart's cursor is
// marked, a row clicked shows its trade on the chart.
type Link = { highlighted: number | null; onPick: (index: number) => void };

type RowProps = { trade: TradeRow; columns: Columns; style: DateStyle; daily: boolean; link: Link };

function Row({ trade, columns, style, daily, link }: RowProps) {
  const pick = () => link.onPick(trade.index);
  return (
    <tr
      tabIndex={0}
      title="Show on the chart"
      aria-current={link.highlighted === trade.index ? 'true' : undefined}
      onClick={pick}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        pick();
      }}
      className="cursor-pointer outline-none hover:bg-muted focus-visible:bg-muted aria-[current]:bg-muted"
    >
      {columns.side && <td className={CELL}>{trade.long ? 'Long' : 'Short'}</td>}
      <td className={cn(CELL, TIME)}>
        <CandleTime seconds={trade.openTs} style={style} daily={daily} />
      </td>
      <td className={cn(CELL, NUMBER, PRICE)}>{formatPrice(trade.openPrice)}</td>
      <td className={cn(CELL, TIME)}>
        <CandleTime seconds={trade.closeTs} style={style} daily={daily} />
      </td>
      <td className={cn(CELL, NUMBER, PRICE)}>{formatPrice(trade.closePrice)}</td>
      <td className={cn(CELL, NUMBER, TONE[returnTone(trade.return)])}>{formatPercent(trade.return)}</td>
      <td className={cn(CELL, NUMBER, WIDE)}>{formatCount(trade.candles)}</td>
      <td className={cn(CELL, 'text-muted-foreground', WIDE)}>{trade.reason}</td>
    </tr>
  );
}

type TableProps = { rows: readonly TradeRow[]; columns: Columns; daily: boolean; link: Link };

function Table({ rows, columns, daily, link }: TableProps) {
  const style = useDateStyle();
  return (
    <table className="w-full border-collapse text-[12.5px]">
      <thead>
        <tr>
          {columns.side && <th className={HEAD}>Side</th>}
          <th className={HEAD}>{columns.opened}</th>
          <th className={cn(HEAD, 'text-right', PRICE)}>at</th>
          <th className={HEAD}>{columns.closed}</th>
          <th className={cn(HEAD, 'text-right', PRICE)}>at</th>
          <th className={cn(HEAD, 'text-right')}>Return</th>
          <th className={cn(HEAD, 'text-right', WIDE)}>Candles</th>
          <th className={cn(HEAD, WIDE)}>Why</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((trade) => (
          <Row key={trade.index} trade={trade} columns={columns} style={style} daily={daily} link={link} />
        ))}
      </tbody>
    </table>
  );
}

type Props = { trades: Trades; longOnly: boolean; daily: boolean } & Link;

// The trades, the newest first: six of them, all of them after Show all. A run with more than the
// light view keeps says how many it shows. Clicking a trade shows it on the chart.
export function TradesBox({ trades, longOnly, daily, highlighted, onPick }: Props) {
  const [all, setAll] = useState(false);
  const rows = tradeRows(trades);
  const shown = all ? rows : rows.slice(0, FIRST_TRADES);
  const toggle = rows.length > FIRST_TRADES && (
    <Button
      type="button"
      variant="ghost"
      className={cn(BUTTON_SIZES.sm, GHOST_BUTTON, 'font-medium')}
      onClick={() => setAll(!all)}
    >
      {all ? 'Show less' : 'Show all'}
    </Button>
  );
  return (
    <ResultBox
      title={
        <>
          Trades <span className="font-normal text-muted-foreground">{formatCount(trades.total)}</span>
        </>
      }
      aside={toggle}
    >
      {all && !trades.complete && (
        <p className={HINT}>
          Newest {formatCount(trades.count)} of {formatCount(trades.total)}. All in the JSON.
        </p>
      )}
      {rows.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">No trades</p>
      ) : (
        <div
          className={cn('@container -mx-4 -mb-3.5 overflow-x-auto border-t', all && 'max-h-[480px] overflow-y-auto')}
        >
          <Table rows={shown} columns={columnsOf(longOnly)} daily={daily} link={{ highlighted, onPick }} />
        </div>
      )}
    </ResultBox>
  );
}
