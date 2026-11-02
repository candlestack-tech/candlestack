import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { experimentsQueryOptions } from '@/api';
import { Hint } from '@/components/Hint';
import { SearchBox } from '@/components/SearchBox';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

import { AuthorAvatar } from '../experiments/AuthorAvatar';
import { experimentTarget } from '../experiments/format';
import { compareTarget } from '../format';
import { BacktestNumber, BacktestReturn, Missing, ReturnValue, StatusBadge } from '../parts';
import { useBacktestsPage } from '../useBacktests';
import { CompareToggle } from './CompareToggle';
import { useCompareSelection, SLOTS } from './selection';

type Props = { open: boolean; onOpenChange: (open: boolean) => void };
type Tab = 'yours' | 'experiments';

const ROW =
  'grid grid-cols-[52px_minmax(60px,110px)_minmax(0,1fr)_96px_36px] items-center gap-2 border-b py-1 text-[13px] last:border-b-0';
const LIST = 'max-h-[50vh] overflow-y-auto border-t';
const NONE = 'p-6 text-center text-muted-foreground';

function Yours({ query }: { query: string }) {
  const { data } = useBacktestsPage(query ? { q: query } : {});
  if (!data) return <Missing />;
  const more = data.total - data.backtests.length;
  return (
    <>
      <div className={LIST}>
        {data.backtests.map((backtest) => (
          <div key={backtest.id} className={ROW}>
            <BacktestNumber backtest={backtest} />
            <span className="truncate font-medium">{compareTarget(backtest).symbol}</span>
            <span title={backtest.name ?? backtest.model.name} className="truncate text-muted-foreground">
              {backtest.name ?? backtest.model.name}
            </span>
            <span className="text-right font-mono">
              {backtest.status === 'finished' ? (
                <BacktestReturn backtest={backtest} />
              ) : (
                <StatusBadge status={backtest.status} />
              )}
            </span>
            <CompareToggle target={compareTarget(backtest)} />
          </div>
        ))}
        {data.backtests.length === 0 && <p className={NONE}>No backtests match</p>}
      </div>
      {more > 0 && <Hint>{more} more. Search to find them.</Hint>}
    </>
  );
}

// The other accounts' published backtests, the newest first: the author by initials and name
// before the model (public-experiments, MODALS.picker).
function FromExperiments({ query }: { query: string }) {
  const { data } = useQuery(experimentsQueryOptions({ q: query || undefined }));
  if (!data) return <Missing />;
  const others = data.experiments.filter((experiment) => !experiment.author.mine);
  const more = data.total - data.experiments.length;
  return (
    <>
      <div className={LIST}>
        {others.map((experiment) => {
          const target = experimentTarget(experiment);
          const who = `${experiment.author.name} · ${experiment.model.name}`;
          return (
            <div key={experiment.id} className={ROW}>
              <BacktestNumber backtest={experiment} />
              <span className="truncate font-mono font-medium">{target.symbol}</span>
              <span title={who} className="flex min-w-0 items-center gap-1.5 text-muted-foreground">
                <AuthorAvatar name={experiment.author.name} size="xxs" />
                <span className="truncate">{who}</span>
              </span>
              <span className="text-right font-mono">
                {experiment.total_return === null ? <Missing /> : <ReturnValue value={experiment.total_return} />}
              </span>
              <CompareToggle target={target} />
            </div>
          );
        })}
        {others.length === 0 && <p className={NONE}>No backtests match</p>}
      </div>
      {more > 0 && <Hint>{more} more. Search to find them.</Hint>}
    </>
  );
}

// Yours and Experiments with how many each has: the user's backtests and the other accounts'
// published ones (.st-tabs).
function Tabs({ tab, onTab }: { tab: Tab; onTab: (tab: Tab) => void }) {
  const yours = useBacktestsPage({}).data?.counts.all;
  const counts = useQuery(experimentsQueryOptions({})).data?.counts;
  const theirs = counts && counts.all - counts.mine;
  const tabs = [
    ['yours', 'Yours', yours],
    ['experiments', 'Experiments', theirs],
  ] as const;
  return (
    <div role="tablist" aria-label="Whose backtests" className="flex flex-wrap gap-0.5 border-b">
      {tabs.map(([value, label, count]) => (
        <button
          key={value}
          type="button"
          role="tab"
          aria-selected={tab === value}
          onClick={() => {
            onTab(value);
          }}
          className={cn(
            'relative inline-flex cursor-pointer items-baseline gap-[7px] px-3 pt-[9px] pb-[11px] text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground',
            tab === value &&
              'font-medium text-foreground after:absolute after:inset-x-2.5 after:-bottom-px after:h-0.5 after:rounded-sm after:bg-primary',
          )}
        >
          {label}
          {count !== undefined && <span className="font-mono text-xs font-normal text-muted-foreground">{count}</span>}
        </button>
      ))}
    </div>
  );
}

// The title with how many are chosen.
function PickerHead() {
  const { picks } = useCompareSelection();
  return (
    <DialogHeader>
      <DialogTitle>
        Add backtests{' '}
        <span className="ml-1 font-mono text-xs font-medium text-muted-foreground">
          {picks.length} of {SLOTS}
        </span>
      </DialogTitle>
    </DialogHeader>
  );
}

// The tabs, the search and the list of the open tab; another tab starts its search over.
function Lists() {
  const [tab, setTab] = useState<Tab>('yours');
  const [query, setQuery] = useState('');
  const yours = tab === 'yours';
  return (
    <div className="flex flex-col gap-3">
      <Tabs
        tab={tab}
        onTab={(next) => {
          setTab(next);
          setQuery('');
        }}
      />
      <SearchBox
        key={tab}
        query={query}
        onSearch={setQuery}
        placeholder={yours ? 'Number, instrument or model, e.g. aapl' : 'Instrument, model or author'}
        label={yours ? 'Search backtests' : 'Search experiments'}
        maxLength={100}
        debounceMs={300}
      />
      {yours ? <Yours query={query} /> : <FromExperiments query={query} />}
    </div>
  );
}

// Adding backtests without leaving the page: the user's own, newest first, or the other accounts'
// from Experiments, or the ones a search finds. It opens on Yours.
export function ComparePicker({ open, onOpenChange }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <PickerHead />
        <DialogBody>{open && <Lists />}</DialogBody>
        <DialogFooter>
          <Button
            type="button"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
