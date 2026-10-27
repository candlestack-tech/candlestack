import { Link } from '@tanstack/react-router';
import { ArrowDown, ArrowUp } from 'lucide-react';
import type { ReactNode } from 'react';

import { exchangeName, type Instrument, type Market } from '@/api';
import { InstrumentIcon } from '@/components/InstrumentIcon';
import { useDateStyle } from '@/features/preferences';
import { formatMonth, marketTimeZone } from '@/lib/dates';
import { changeTone, formatChange, formatPrice, formatSignedChange, formatVolume } from '@/lib/numbers';
import { cn } from '@/lib/utils';
import type { ListSort } from '@/routes/instrumentsSearch';

import { MiniCandles } from './MiniCandles';
import { sortedColumn, type SortedColumn } from './sorting';

// The columns follow the table's own width, not the screen's, since the list sits beside the
// app's sidebar: below 740 px three columns, the symbol, the last 7 daily candles and the price;
// from 740 px the stats get their own columns and the name the rest; from 880 px the 10 daily
// candles too, from 1000 px since when there is data, from 1180 px the type. Cells have 12 px at the sides (8 px between the columns of
// a phone), 4 px above and below the 32 px icon and the 32 px candles: a row is 41 px high.
const HEAD = 'py-2 text-xs font-medium text-muted-foreground';
const CELL = 'py-1';
const PHONE_FIRST = 'pr-2 pl-3 @min-[740px]:px-3';
const PHONE_LAST = 'pr-3 pl-2 @min-[740px]:px-3';
const PHONE_MIDDLE = 'w-[60px] px-2 @min-[740px]:hidden';
const WIDEST = 'hidden w-[136px] px-3 @min-[880px]:table-cell';
const WIDE = 'hidden px-3 @min-[740px]:table-cell';
const XL = 'hidden px-3 @min-[1000px]:table-cell';
const TONES = { up: 'text-up', down: 'text-down', flat: 'text-muted-foreground', none: 'text-muted-foreground' };
const TYPES: Record<Market, string> = { stock: 'Stock', crypto: 'Crypto' };

// The 12 px arrow of the sorted column.
function Arrow({ direction }: { direction: SortedColumn['direction'] }) {
  const Icon = direction === 'descending' ? ArrowDown : ArrowUp;
  return <Icon aria-hidden className="size-3 shrink-0" />;
}

type HeadProps = { className: string; sorted?: SortedColumn['direction'] | undefined; children: ReactNode };

// A column header; the sorted one is ink with its arrow.
function Head({ className, sorted, children }: HeadProps) {
  return (
    <th scope="col" aria-sort={sorted ?? 'none'} className={cn(HEAD, className, sorted && 'text-foreground')}>
      {children}
    </th>
  );
}

// A header's text and arrow on one line, right-aligned in the number columns.
function Label({ right, arrow, children }: { right?: boolean; arrow?: ReactNode; children: ReactNode }) {
  return (
    <span className={cn('flex items-center gap-1 whitespace-nowrap', right && 'justify-end')}>
      <span className="truncate">{children}</span>
      {arrow}
    </span>
  );
}

// The three headers of a phone, each a header of its own (hidden from md up) so that its aria-sort
// can name the order the phone shows: the symbol column stands for the volume column a phone
// hides ("Symbol · by volume ↓") and for the symbol, the price column for the change ("Price ·
// 24h ↓").
function PhoneHeads({ sorted }: { sorted: SortedColumn }) {
  const { column, direction } = sorted;
  const arrow = <Arrow direction={direction} />;
  return (
    <>
      <Head
        className={cn(PHONE_FIRST, 'text-left @min-[740px]:hidden')}
        sorted={column === 'change' ? undefined : direction}
      >
        <Label arrow={column !== 'change' && arrow}>{column === 'volume' ? 'Symbol · by volume' : 'Symbol'}</Label>
      </Head>
      <Head className={cn(PHONE_MIDDLE, 'text-left')}>7d</Head>
      <Head
        className={cn(PHONE_LAST, 'w-[100px] text-right @min-[740px]:hidden')}
        sorted={column === 'change' ? direction : undefined}
      >
        <Label right arrow={column === 'change' && arrow}>
          {column === 'change' ? 'Price · 24h' : 'Price'}
        </Label>
      </Head>
    </>
  );
}

function Header({ sort }: { sort: ListSort | undefined }) {
  const sorted = sortedColumn(sort);
  const on = (name: SortedColumn['column']) => (sorted.column === name ? sorted.direction : undefined);
  const arrow = (name: SortedColumn['column']) => on(name) && <Arrow direction={sorted.direction} />;
  return (
    <thead className="border-b">
      <tr>
        <PhoneHeads sorted={sorted} />
        <Head className={cn(WIDE, 'w-[168px] text-left')} sorted={on('symbol')}>
          <Label arrow={arrow('symbol')}>Symbol</Label>
        </Head>
        <Head className={cn(WIDE, 'w-[128px] text-right')}>
          <Label right>Price</Label>
        </Head>
        <Head className={cn(WIDE, 'w-[104px] text-right')} sorted={on('change')}>
          <Label right arrow={arrow('change')}>
            24h
          </Label>
        </Head>
        <Head className={cn(WIDEST, 'text-left')}>10d</Head>
        <Head className={cn(WIDE, 'w-[112px] text-right')} sorted={on('volume')}>
          <Label right arrow={arrow('volume')}>
            Volume 24h
          </Label>
        </Head>
        <Head className={cn(WIDE, 'text-left')}>Name</Head>
        <Head className="hidden w-[88px] px-3 text-left @min-[1180px]:table-cell">Type</Head>
        <Head className={cn(WIDE, 'w-[104px] text-left')}>Exchange</Head>
        <Head className={cn(XL, 'w-[96px] text-left')}>Data since</Head>
      </tr>
    </thead>
  );
}

// The 24 h change after an 8 px triangle that shows its direction, without a sign.
function Change({ change }: { change: number | null }) {
  const tone = changeTone(change);
  return (
    <span className={cn('inline-flex items-center justify-end gap-0.5 font-mono', TONES[tone])}>
      {tone === 'up' && (
        <svg aria-hidden viewBox="0 0 8 8" className="size-2 shrink-0 fill-current">
          <path d="M4 1 7.5 7h-7Z" />
        </svg>
      )}
      {tone === 'down' && (
        <svg aria-hidden viewBox="0 0 8 8" className="size-2 shrink-0 fill-current">
          <path d="M4 7 .5 1h7Z" />
        </svg>
      )}
      {formatChange(change)}
    </span>
  );
}

// The icon and the symbol, the row's link; on a phone "exchange · volume" under it, as the
// exchange and volume columns are hidden there.
function SymbolCell({ instrument, where }: { instrument: Instrument; where: string }) {
  return (
    <td className={cn(CELL, PHONE_FIRST)}>
      <div className="flex items-center gap-2.5">
        <InstrumentIcon id={instrument.id} size={32} />
        <div className="min-w-0">
          <Link
            to="/instrument/$id"
            params={{ id: instrument.id }}
            title={instrument.symbol}
            className="block truncate font-mono leading-[18px] outline-none after:absolute after:inset-0 focus-visible:text-primary @min-[740px]:leading-5"
          >
            {instrument.symbol}
          </Link>
          <span
            title={where}
            className="block truncate text-xs leading-[14px] text-muted-foreground @min-[740px]:hidden"
          >
            {where}
          </span>
        </div>
      </div>
    </td>
  );
}

// The price; on a phone the change under it, with its sign.
function PriceCell({ instrument, stats }: { instrument: Instrument; stats: boolean }) {
  return (
    <td className={cn(CELL, PHONE_LAST, 'text-right')}>
      <span
        className={cn(
          'block truncate font-mono leading-[18px] @min-[740px]:leading-5',
          !stats && 'text-muted-foreground',
        )}
      >
        {formatPrice(instrument.price)}
      </span>
      <span
        className={cn(
          'block truncate text-xs leading-[14px] @min-[740px]:hidden',
          stats ? ['font-mono', TONES[changeTone(instrument.change_24h)]] : 'text-muted-foreground',
        )}
      >
        {stats ? formatSignedChange(instrument.change_24h) : 'No stats yet'}
      </span>
    </td>
  );
}

// Text longer than its column is cut short; its title shows it in full. Without stats the price,
// change and volume are a dash (on a phone, "no stats yet").
function Row({ instrument }: { instrument: Instrument }) {
  const exchange = exchangeName(instrument);
  const stats = instrument.price !== null;
  const where = `${exchange} · ${stats ? formatVolume(instrument.volume_24h) : 'no stats yet'}`;
  return (
    <tr className="relative border-b last:border-b-0 hover:bg-accent">
      <SymbolCell instrument={instrument} where={where} />
      <td className={cn(CELL, PHONE_MIDDLE)}>
        <MiniCandles id={instrument.id} spark={instrument.spark} count={7} width={44} height={24} />
      </td>
      <PriceCell instrument={instrument} stats={stats} />
      <td className={cn(CELL, WIDE, 'text-right')}>
        <Change change={instrument.change_24h} />
      </td>
      <td className={cn(CELL, WIDEST)}>
        <MiniCandles id={instrument.id} spark={instrument.spark} count={10} width={112} height={32} hover />
      </td>
      <td className={cn(CELL, WIDE, 'text-right font-mono', instrument.volume_24h === null && 'text-muted-foreground')}>
        {formatVolume(instrument.volume_24h)}
      </td>
      <td className={cn(CELL, WIDE, 'text-muted-foreground')}>
        <span title={instrument.name} className="block truncate">
          {instrument.name}
        </span>
      </td>
      <td className={cn(CELL, 'hidden truncate px-3 text-muted-foreground @min-[1180px]:table-cell')}>
        {TYPES[instrument.market]}
      </td>
      <td title={exchange} className={cn(CELL, WIDE, 'truncate text-muted-foreground')}>
        {exchange}
      </td>
      <SinceCell instrument={instrument} />
    </tr>
  );
}

// The month of the first candle, Aug 2017, in the zone the instrument page writes its first day
// in; a dash while the server does not know it yet (it looks it up for a later visit).
function SinceCell({ instrument }: { instrument: Instrument }) {
  const { timeZone } = useDateStyle(marketTimeZone(instrument.market));
  const since = instrument.available_from;
  return (
    <td className={cn(CELL, XL, 'font-mono text-[13px] whitespace-nowrap text-muted-foreground')}>
      {since === null ? <span title="Not known yet">—</span> : formatMonth(new Date(since * 1000), { timeZone })}
    </td>
  );
}

// One row per instrument with its 24 h stats (#184), its last daily candles (#189) and since when
// there is data (#199): Symbol 168 | Price 128 | 24h 104 | 10d 136 | Volume 24h 112 | Name (the
// rest) | Type 88 | Exchange 104 | Data since 96 px. The symbol is the row's link, stretched over
// the whole row, so the row is one click target and one tab stop. The columns have fixed widths
// and the name takes the rest, so long text truncates instead of widening the table past the
// table (the mockup scrolls it sideways instead): each column shows from the table width (above)
// where it still leaves the name about 150 px; a phone shows the symbol over "exchange · volume", the last 7
// candles in 60 px and the price over the change. The sorted column's header is ink with an arrow.
export function InstrumentTable({ items, sort }: { items: readonly Instrument[]; sort?: ListSort | undefined }) {
  return (
    <div className="@container">
      <table className="w-full table-fixed border-collapse text-sm">
        <Header sort={sort} />
        <tbody>
          {items.map((instrument) => (
            <Row key={instrument.id} instrument={instrument} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
