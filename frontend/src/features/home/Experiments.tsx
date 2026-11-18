import { Link } from '@tanstack/react-router';

import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { Button } from '@/components/ui/button';
import { Avatar } from '@/features/account';
import { cn } from '@/lib/utils';

import { Checks, ExampleLine } from './parts';
import { Section, SectionIntro } from './Section';

// Three published backtests as the Experiments page shows them (ExperimentCard), with sample
// figures written here and never fetched, as market data is for signed-in accounts only (#340).
// The lines are the return in percent over the period, the strategy's and buy and hold's.
type Card = {
  name: string;
  description: string;
  tile: { color: string; text: string };
  model: string;
  input: string;
  totalReturn: string;
  sharpe: string;
  maxDrawdown: string;
  trades: string;
  author: string;
  day: string;
  strategy: readonly number[];
  buyAndHold: { value: string; line: readonly number[] };
};

const CARDS: readonly Card[] = [
  {
    name: 'Quiet Otter',
    description: 'AAPL · Kagi · 1h',
    tile: { color: '#555555', text: 'A' },
    model: 'lstm-kagi-v3',
    input: 'in 25 × 6',
    totalReturn: '+18.4 %',
    sharpe: '1.21',
    maxDrawdown: '−7.9 %',
    trades: '142',
    author: 'Jana Kováčová',
    day: 'Today',
    strategy: [0, -1, -3, -2, -2, -3, 2, 1, 6, 5, 5, 2, 2, 0, 1, 5, 6, 12, 12, 15, 18],
    buyAndHold: {
      value: '+12.0 %',
      line: [0, -3, -3, -5, -1, -5, -4, -7, -4, 1, -1, -2, -2, -4, 0, 1, 5, 5, 7, 10, 12],
    },
  },
  {
    name: 'Bright Falcon',
    description: 'BTCUSDT · Renko · 15m',
    tile: { color: '#f7931a', text: '₿' },
    model: 'tsmixer-renko',
    input: 'in 32 × 8',
    totalReturn: '+27.9 %',
    sharpe: '0.94',
    maxDrawdown: '−18.3 %',
    trades: '611',
    author: 'Tomáš Bielik',
    day: 'Yesterday',
    strategy: [0, 7, 9, 10, 10, 7, 9, 6, 20, 21, 21, 26, 31, 20, 29, 37, 50, 44, 46, 49, 28],
    buyAndHold: {
      value: '+31.5 %',
      line: [0, -1, 7, 9, 15, 14, 15, 21, 26, 22, 25, 28, 22, 17, 20, 20, 27, 24, 33, 34, 32],
    },
  },
  {
    name: 'Calm Lynx',
    description: 'SPY · Candles · 1d',
    tile: { color: '#2c3e8f', text: 'SPY' },
    model: 'conv1d-v2',
    input: 'in 60 × 5',
    totalReturn: '+9.6 %',
    sharpe: '0.88',
    maxDrawdown: '−6.2 %',
    trades: '37',
    author: 'Eva Molnárová',
    day: '14 Nov',
    strategy: [0, -3, -5, -6, -4, -6, -5, -5, -4, -1, -3, 0, 2, 3, 4, 2, 7, 7, 9, 12, 10],
    buyAndHold: { value: '+14.2 %', line: [0, 1, 3, 5, 7, 8, 6, 9, 11, 8, 11, 13, 10, 10, 12, 12, 13, 12, 10, 11, 14] },
  },
];

// A line in the 300 × 40 box, both lines on one scale with 2 px above and below.
function path(line: readonly number[], low: number, high: number): string {
  const step = 300 / (line.length - 1);
  return line
    .map(
      (value, index) =>
        `${index ? 'L' : 'M'}${(index * step).toFixed(1)} ${(38 - ((value - low) / (high - low)) * 36).toFixed(1)}`,
    )
    .join('');
}

// The return over the card's width, buy and hold dashed under it, and their key.
function Chart({ card }: { card: Card }) {
  const both = [...card.strategy, ...card.buyAndHold.line];
  const low = Math.min(...both);
  const high = Math.max(...both);
  return (
    <>
      {/* Stretched to the card's width; the strokes stay 1 and 1.5 px. */}
      <svg viewBox="0 0 300 40" preserveAspectRatio="none" aria-hidden="true" className="mt-2.5 block h-11 w-full">
        <path
          d={path(card.buyAndHold.line, low, high)}
          strokeDasharray="3 3"
          className="fill-none stroke-muted-foreground stroke-1 [vector-effect:non-scaling-stroke]"
        />
        <path
          d={path(card.strategy, low, high)}
          className="fill-none stroke-up stroke-[1.5] [vector-effect:non-scaling-stroke]"
        />
      </svg>
      <div className="mt-[3px] flex justify-between gap-2 font-mono text-[11px] whitespace-nowrap text-muted-foreground">
        <span>
          <i className="mr-[5px] inline-block w-3 border-t-[1.5px] border-up align-middle" />
          Strategy
        </span>
        <span className="min-w-0 truncate">
          <i className="mr-[5px] inline-block w-3 border-t border-dashed border-muted-foreground align-middle" />
          Buy and hold {card.buyAndHold.value}
        </span>
      </div>
    </>
  );
}

// Return, Sharpe, Max drawdown and Trades in a row, the last at the right.
function Numbers({ card }: { card: Card }) {
  const numbers = [
    ['Return', card.totalReturn, 'text-up'],
    ['Sharpe', card.sharpe, undefined],
    ['Max DD', card.maxDrawdown, 'text-down'],
    ['Trades', card.trades, undefined],
  ] as const;
  return (
    <dl className="my-2.5 grid grid-cols-[repeat(4,auto)] justify-between gap-1.5">
      {numbers.map(([term, value, tone]) => (
        <div key={term} className="last:text-right">
          <dt className="text-[11px] whitespace-nowrap text-muted-foreground">{term}</dt>
          <dd className={cn('font-mono text-[13px] font-semibold whitespace-nowrap tabular-nums', tone)}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ExampleCard({ card }: { card: Card }) {
  return (
    <article className="flex min-w-0 flex-col rounded-[10px] border bg-background p-3.5">
      <div className="flex min-w-0 items-center gap-2.5">
        <span
          aria-hidden="true"
          className="grid size-7 flex-none place-items-center rounded-md text-[10px] font-bold text-white"
          style={{ background: card.tile.color }}
        >
          {card.tile.text}
        </span>
        <div className="min-w-0">
          <h3 className="truncate text-[15px]/5 font-semibold">{card.name}</h3>
          <p className="truncate font-mono text-[11.5px] text-muted-foreground">{card.description}</p>
        </div>
      </div>
      <p className="mt-2.5 flex min-w-0 items-baseline gap-2 text-[13px] font-medium">
        <span className="truncate">{card.model}</span>
        <span className="font-mono text-[11.5px] font-normal whitespace-nowrap text-muted-foreground">
          {card.input}
        </span>
      </p>
      <Chart card={card} />
      <Numbers card={card} />
      <div className="mt-auto flex min-w-0 items-center gap-2 border-t pt-[9px] text-[12.5px]">
        <Avatar user={{ display_name: card.author, email: '' }} className="size-[22px] text-[9px]" />
        <span className="min-w-0 truncate">{card.author}</span>
        <span className="ml-auto text-[11.5px] whitespace-nowrap text-muted-foreground">{card.day}</span>
      </div>
    </article>
  );
}

// The public experiments: what the page holds, the way there, and three cards with sample
// figures, side by side from 981 px.
export function Experiments() {
  return (
    <Section id="experiments" split>
      <div>
        <SectionIntro
          eyebrow="Experiments"
          title="See what others published"
          text="Backtests their authors chose to share, each with its instrument, representation, model and decision settings and the same metrics. Open one to see its trades and what the model reads."
        />
        <Checks items={['Filter by market, representation and timeframe', 'Add any of them to your Compare']} />
        <Button asChild variant="outline" className={cn(BUTTON_SIZES.default, OUTLINE_BUTTON, 'mt-5')}>
          <Link to="/experiments">Open Experiments</Link>
        </Button>
      </div>
      <div>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-2.5 min-[981px]:grid-cols-3">
          {CARDS.map((card) => (
            <ExampleCard key={card.name} card={card} />
          ))}
        </div>
        <ExampleLine>Sample figures, not market data</ExampleLine>
      </div>
    </Section>
  );
}
