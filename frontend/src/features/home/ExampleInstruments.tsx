import { exchangeLabel, type Exchange, type Instrument } from '@/api';
import { InstrumentIcon } from '@/components/InstrumentIcon';
import { MiniCandles } from '@/features/instruments';
import { changeTone, formatChange, formatPrice, formatSignedChange } from '@/lib/numbers';
import { cn } from '@/lib/utils';

type Example = { id: string; symbol: string; exchange: Exchange; price: number; change: number; closes: number[] };

// Sample figures, written here and never fetched: market data is for signed-in accounts only
// (#340). Each row's 10 days are 11 closes, every day opening at the close before it; only the
// shape matters, the candles are scaled to their own low and high. The icons are the public
// logos, not market data.
const EXAMPLES: Example[] = [
  {
    id: 'crypto:BTCUSDT',
    symbol: 'BTCUSDT',
    exchange: 'BINANCE',
    price: 68_240,
    change: 2.1,
    closes: [100, 101, 100.5, 102, 103, 102.4, 104, 105, 104.2, 106, 107],
  },
  {
    id: 'stock:AAPL',
    symbol: 'AAPL',
    exchange: 'NASDAQ',
    price: 214.3,
    change: 0.8,
    closes: [100, 100.4, 100.1, 100.8, 100.5, 101, 101.3, 100.9, 101.5, 101.8, 102],
  },
  {
    id: 'crypto:ETHUSDT',
    symbol: 'ETHUSDT',
    exchange: 'BINANCE',
    price: 3_520,
    change: -1.4,
    closes: [100, 101, 102, 101.2, 100.5, 101, 99.5, 98.8, 99.2, 98, 97.5],
  },
  {
    id: 'stock:TSLA',
    symbol: 'TSLA',
    exchange: 'NASDAQ',
    price: 251.7,
    change: 3.2,
    closes: [100, 99, 101, 102.5, 101.8, 104, 103, 105.5, 106, 105, 108],
  },
  {
    id: 'stock:SPY',
    symbol: 'SPY',
    exchange: 'ARCA',
    price: 548.9,
    change: 0.5,
    closes: [100, 100.3, 100.1, 100.6, 100.9, 100.7, 101.2, 101, 101.4, 101.7, 101.9],
  },
];

const DAY = 86_400;
const OCT_1 = 1_790_812_800; // 2026-10-01 00:00 UTC

// The 10 days as the list's candles take them, [open time, open, high, low, close], with wicks of
// a few sizes beyond the bodies.
function sparkOf(closes: number[]): Instrument['spark'] {
  return closes.slice(1).map((close, index) => {
    const open = closes[index] ?? close;
    const wick = 0.2 + (index % 3) * 0.15;
    return [OCT_1 + index * DAY, open, Math.max(open, close) + wick, Math.min(open, close) - wick, close];
  });
}

// The list's row with fewer columns: Symbol (the rest) | Exchange 96 | 10d 136 | Price 112 | 24h
// 92 px from sm (640 px); a phone shows the symbol, the last 7 candles in 60 px and the price over
// the change in 104 px. Cells have 12 px at the sides and 8 px between the columns of a phone,
// 4 px above and below the 32 px icon: a row is 41 px high, as in the list.
const HEAD = 'overflow-hidden py-2 text-xs font-medium whitespace-nowrap text-muted-foreground';
const CELL = 'py-1';
const FIRST = 'pr-2 pl-3 sm:px-3';
const MIDDLE = 'px-2 sm:px-3';
const PHONE_LAST = 'pr-3 pl-2 sm:px-3';
const WIDE = 'hidden px-3 sm:table-cell';
const TONES = { up: 'text-up', down: 'text-down', flat: 'text-muted-foreground', none: 'text-muted-foreground' };

function Header() {
  return (
    <thead className="border-b">
      <tr>
        <th scope="col" className={cn(HEAD, FIRST, 'text-left')}>
          Symbol
        </th>
        <th scope="col" className={cn(HEAD, WIDE, 'w-[96px] text-left')}>
          Exchange
        </th>
        <th scope="col" className={cn(HEAD, MIDDLE, 'w-[60px] text-left sm:w-[136px]')}>
          <span className="hidden sm:inline">10d</span>
          <span className="sm:hidden">7d</span>
        </th>
        <th scope="col" className={cn(HEAD, PHONE_LAST, 'w-[104px] text-right sm:w-[112px]')}>
          <span className="hidden sm:inline">Price</span>
          <span className="sm:hidden">Price · 24h</span>
        </th>
        <th scope="col" className={cn(HEAD, WIDE, 'w-[92px] text-right')}>
          24h
        </th>
      </tr>
    </thead>
  );
}

// The 24 h change after an 8 px triangle that shows its direction, without a sign, as in the list.
function Change({ change }: { change: number }) {
  const tone = changeTone(change);
  return (
    <span className={cn('inline-flex items-center justify-end gap-0.5 font-mono whitespace-nowrap', TONES[tone])}>
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

// The price; on a phone the change under it, with its sign.
function PriceCell({ example }: { example: Example }) {
  return (
    <td className={cn(CELL, PHONE_LAST, 'text-right')}>
      <span className="block truncate font-mono leading-[18px]">{formatPrice(example.price)}</span>
      <span
        className={cn('block truncate font-mono text-xs leading-[14px] sm:hidden', TONES[changeTone(example.change)])}
      >
        {formatSignedChange(example.change)}
      </span>
    </td>
  );
}

// A row as the list draws it, but nothing in it is a link or reacts to the pointer.
function Row({ example }: { example: Example }) {
  const exchange = exchangeLabel(example.exchange);
  const spark = sparkOf(example.closes);
  return (
    <tr className="border-b">
      <td className={cn(CELL, FIRST)}>
        <div className="flex items-center gap-2.5">
          <InstrumentIcon id={example.id} size={32} />
          <span className="min-w-0 truncate font-mono leading-[18px] font-medium sm:leading-5">{example.symbol}</span>
        </div>
      </td>
      <td className={cn(CELL, WIDE)}>
        <span className="block truncate text-muted-foreground">{exchange}</span>
      </td>
      <td className={cn(CELL, MIDDLE)}>
        <MiniCandles id={example.id} spark={spark} count={10} width={112} height={32} className="hidden sm:block" />
        <MiniCandles id={example.id} spark={spark} count={7} width={44} height={24} className="sm:hidden" />
      </td>
      <PriceCell example={example} />
      <td className={cn(CELL, WIDE, 'text-right')}>
        <Change change={example.change} />
      </td>
    </tr>
  );
}

// The home page's instruments (#340): five sample rows in the instrument list's style, so a
// visitor sees what the list shows without any market data, then a line that says they are an
// example. In a box with a solid background, so the page's grid does not show through.
export function ExampleInstruments() {
  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full table-fixed border-collapse text-sm">
        <Header />
        <tbody>
          {EXAMPLES.map((example) => (
            <Row key={example.id} example={example} />
          ))}
        </tbody>
      </table>
      <div className="flex items-center justify-between gap-3 px-3 py-2 text-xs text-muted-foreground">
        <span className="rounded-full border px-2 py-0.5 font-mono text-[11px]/4 font-medium tracking-[0.06em]">
          EXAMPLE
        </span>
        <span>Sample figures, not market data</span>
      </div>
    </div>
  );
}
