import { cn } from '@/lib/utils';

// A fact: what it is over its value, in a colour when it has one (a return).
export type Fact = { term: string; value: string; className?: string };

// Numbers side by side in one framed strip (mockups/app, .p5-facts), each at least 110 px wide and
// wrapping to the next line when the row is full: a model's facts, a backtest's first results.
export function FactsStrip({ facts }: { facts: readonly Fact[] }) {
  return (
    <dl className="mb-4 flex flex-wrap gap-px overflow-hidden rounded-[10px] border bg-border">
      {facts.map(({ term, value, className }) => (
        <div key={term} className="min-w-0 flex-[1_1_110px] bg-background px-3.5 py-2.5">
          <dt className="text-xs text-muted-foreground">{term}</dt>
          <dd className={cn('mt-0.5 font-mono text-[15px] font-medium whitespace-nowrap tabular-nums', className)}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
