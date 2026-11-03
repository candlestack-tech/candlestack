import { Scale } from 'lucide-react';
import type { ReactNode } from 'react';

import { InstrumentIcon } from '@/components/InstrumentIcon';
import { MiniChart } from '@/components/MiniChart';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentDay } from '@/lib/dates';
import { formatCount, formatPercent, formatSharpe, returnTone } from '@/lib/results';
import { cn } from '@/lib/utils';

import { CompareToggle } from '../compare/CompareToggle';
import { instrumentId, instrumentList, returnClass, symbolsOf } from '../format';
import { ModelName } from '../parts';
import { RowOpener } from '../table';
import { AuthorAvatar } from './AuthorAvatar';
import type { CardFacts } from './format';

// The neutral 11 px mono tag of what the model reads (.badge), the same for every representation.
function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-block rounded-[4px] border px-1.5 py-px font-mono text-[11px]/4 font-medium whitespace-nowrap text-muted-foreground">
      {children}
    </span>
  );
}

// The instrument's 28 px tile, its symbol in the 16 px title and the number (.sec-h .dd-h, .xc-id);
// on a live card the symbol is the link stretched over the whole card.
function Title({ facts, live }: { facts: CardFacts; live: boolean }) {
  const symbol = instrumentList(symbolsOf(facts));
  const [first] = facts.instruments;
  const label = `Backtest #${facts.number}, ${symbol}, by ${facts.author.name}`;
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      {first && <InstrumentIcon id={instrumentId(first)} size={28} />}
      <h2 title={symbol} className="truncate text-base font-semibold">
        {live ? (
          <RowOpener backtest={facts} label={label}>
            {symbol}
          </RowOpener>
        ) : (
          symbol
        )}
      </h2>
      <span className="font-mono text-[13px] text-muted-foreground">#{facts.number}</span>
    </div>
  );
}

// The model by its mark and name, then what it reads and its input (.xc-m, .xc-tags).
function Model({ facts }: { facts: CardFacts }) {
  return (
    <>
      <span title={facts.model.name} className="flex min-w-0 text-[13px] font-medium">
        <ModelName name={facts.model.name} framework={facts.model.framework} />
      </span>
      <div className="mt-1.5 flex items-center gap-1">
        {facts.representation && <Tag>{facts.representation}</Tag>}
        <Tag>{facts.timeframe}</Tag>
        {facts.input && (
          <span className="ml-1 font-mono text-xs whitespace-nowrap text-muted-foreground">in {facts.input}</span>
        )}
      </div>
    </>
  );
}

// The return over the card's width, its benchmark dashed (.xc-ch), and their key (.xc-lg).
function Chart({ facts }: { facts: CardFacts }) {
  const { strategy, benchmark, totalReturn } = facts;
  const falling = totalReturn !== null && returnTone(totalReturn) === 'down';
  return (
    <>
      {strategy ? (
        <MiniChart strategy={strategy} benchmark={benchmark.series ?? undefined} className="mt-3 h-11 w-full" />
      ) : (
        <div aria-hidden="true" className="mt-3 h-11 rounded-[5px] border border-border/70" />
      )}
      <div className="mt-1 flex justify-between gap-2 font-mono text-[11px] whitespace-nowrap text-muted-foreground">
        <span>
          <i
            className={cn(
              'mr-[5px] inline-block w-3 border-t-[1.5px] align-middle',
              falling ? 'border-down' : 'border-up',
            )}
          />
          Strategy
        </span>
        <span className="min-w-0 truncate">
          <i className="mr-[5px] inline-block w-3 border-t border-dashed border-muted-foreground align-middle" />
          {benchmark.label} {formatPercent(benchmark.value)}
        </span>
      </div>
    </>
  );
}

// Return, Sharpe, Max drawdown and Trades in a row, the last at the right (.xc-num).
function Numbers({ facts }: { facts: CardFacts }) {
  const { totalReturn, maxDrawdown } = facts;
  const items = [
    ['Return', formatPercent(totalReturn), totalReturn === null ? undefined : returnClass(totalReturn)],
    ['Sharpe', formatSharpe(facts.sharpe), undefined],
    ['Max drawdown', formatPercent(maxDrawdown), maxDrawdown ? 'text-down' : undefined],
    ['Trades', formatCount(facts.trades), undefined],
  ] as const;
  return (
    <dl className="my-3 grid grid-cols-[repeat(4,auto)] justify-between gap-2">
      {items.map(([term, value, tone]) => (
        <div key={term} className="last:text-right">
          <dt className="text-[11.5px] whitespace-nowrap text-muted-foreground">{term}</dt>
          <dd className={cn('font-mono text-sm/5 font-semibold whitespace-nowrap tabular-nums', tone)}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// The author by initials and name ("you" on the user's own), and the day it was published.
function Foot({ facts }: { facts: CardFacts }) {
  const style = useViewerDateStyle();
  const { author, publishedAt } = facts;
  const day = publishedAt === null ? 'Today' : formatRecentDay(new Date(publishedAt), style, undefined, true);
  return (
    <div className="mt-auto flex min-w-0 items-center gap-2 border-t pt-2.5 text-[13px]">
      <AuthorAvatar name={author.name} size="md" />
      <span className="min-w-0 truncate font-medium">{author.name}</span>
      {author.mine && publishedAt !== null && <span className="text-xs text-muted-foreground">you</span>}
      <span className="ml-auto text-xs whitespace-nowrap text-muted-foreground">{day}</span>
    </div>
  );
}

type Props = {
  facts: CardFacts;
  // The compare button of a live card; the preview of Publish shows the icon only.
  target?: Parameters<typeof CompareToggle>[0]['target'];
};

// A published backtest as a card of Experiments (public-experiments, gCard): the dashboard
// widget's card with the instrument in the icon's place, the model and what it reads, the return
// against its benchmark, four results and the author. The whole card opens the backtest; without
// a `target` it is the preview in the Publish dialog, which opens nothing.
export function ExperimentCard({ facts, target }: Props) {
  const live = target !== undefined;
  return (
    <article
      aria-label={live ? undefined : 'Your card as others will see it'}
      className={cn(
        'relative flex min-w-0 flex-col rounded-lg border bg-card p-4 max-[640px]:p-3',
        live &&
          'transition-colors duration-150 hover:border-[color-mix(in_srgb,var(--accent)_45%,var(--rule))] motion-reduce:transition-none',
      )}
    >
      <div className="mb-2.5 flex items-center justify-between gap-2.5">
        <Title facts={facts} live={live} />
        {target ? (
          <span className="-my-0.5 -mr-1">
            <CompareToggle target={target} />
          </span>
        ) : (
          <span
            aria-hidden="true"
            className="-my-0.5 -mr-1 inline-grid size-8 place-items-center text-muted-foreground"
          >
            <Scale className="size-4" />
          </span>
        )}
      </div>
      <Model facts={facts} />
      <Chart facts={facts} />
      <Numbers facts={facts} />
      <Foot facts={facts} />
    </article>
  );
}
