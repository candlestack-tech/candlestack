import { Scale } from 'lucide-react';

import { InstrumentIcon } from '@/components/InstrumentIcon';
import { MiniChart } from '@/components/MiniChart';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentDay } from '@/lib/dates';
import { formatCount, formatPercent, formatSharpe, returnTone } from '@/lib/results';
import { cn } from '@/lib/utils';

import { CompareToggle } from '../compare/CompareToggle';
import { instrumentId, returnClass } from '../format';
import { BacktestNameLines, ModelName } from '../parts';
import { RowOpener } from '../table';
import { AuthorAvatar } from './AuthorAvatar';
import type { CardFacts } from './format';

// The instrument's 28 px tile beside the name in 15 px over what it ran on, what its model reads
// and the timeframe (backtest-names, .xc-tt); on a live card the name is the link stretched over
// the whole card.
function Title({ facts, live }: { facts: CardFacts; live: boolean }) {
  const [first] = facts.instruments;
  const label = `${facts.name}, ${facts.description}, by ${facts.author.name}`;
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2.5">
      {first && <InstrumentIcon id={instrumentId(first)} size={28} />}
      <BacktestNameLines
        name={facts.name}
        description={facts.description}
        wrap={(shown) => (
          <h2 className="flex min-w-0 text-[15px]/5 font-semibold">
            {live ? (
              <RowOpener backtest={facts} label={label}>
                {shown}
              </RowOpener>
            ) : (
              shown
            )}
          </h2>
        )}
      />
    </div>
  );
}

// The model by its mark and name, then its input: "in 25 × 3" (backtest-names, .xc-m).
function Model({ facts }: { facts: CardFacts }) {
  return (
    <span className="mt-0.5 flex min-w-0 items-center text-[13px] font-medium">
      <span title={facts.model.name} className="flex min-w-0">
        <ModelName name={facts.model.name} framework={facts.model.framework} />
      </span>
      {facts.input && (
        <span className="ml-[9px] font-mono text-xs font-normal whitespace-nowrap text-muted-foreground">
          in {facts.input}
        </span>
      )}
    </span>
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

// A published backtest as a card of Experiments (public-experiments, gCard; backtest-names): the
// dashboard widget's card with the instrument in the icon's place beside the name, the model with
// its input, the return against its benchmark, four results and the author. The whole card opens
// the backtest; without a `target` it is the preview in the Publish dialog, which opens nothing.
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
