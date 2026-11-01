import { useState } from 'react';

import { Hint } from '@/components/Hint';
import { SearchBox } from '@/components/SearchBox';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

import { compareTarget } from '../format';
import { BacktestNumber, BacktestReturn, Missing, StatusBadge } from '../parts';
import { useBacktestsPage } from '../useBacktests';
import { CompareToggle } from './CompareToggle';
import { useCompareSelection, SLOTS } from './selection';

type Props = { open: boolean; onOpenChange: (open: boolean) => void };

function List({ query }: { query: string }) {
  const { data } = useBacktestsPage({ q: query });
  if (!data) return <Missing />;
  const more = data.total - data.backtests.length;
  return (
    <>
      <div className="max-h-[50vh] overflow-y-auto border-t">
        {data.backtests.map((backtest) => (
          <div
            key={backtest.id}
            className="grid grid-cols-[52px_minmax(60px,110px)_minmax(0,1fr)_96px_36px] items-center gap-2 border-b py-1 text-[13px] last:border-b-0"
          >
            <BacktestNumber backtest={backtest} />
            <span className="truncate font-medium">{compareTarget(backtest).symbol}</span>
            <span title={backtest.model.name} className="truncate text-muted-foreground">
              {backtest.model.name}
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
        {data.backtests.length === 0 && <p className="p-6 text-center text-muted-foreground">No backtests match</p>}
      </div>
      {more > 0 && <Hint>{more} more. Search to find them.</Hint>}
    </>
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

// Adding backtests without leaving the page: the newest, or the ones a search finds.
export function ComparePicker({ open, onOpenChange }: Props) {
  const [query, setQuery] = useState('');
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <PickerHead />
        <DialogBody>
          <div className="flex flex-col gap-3">
            <SearchBox
              query={query}
              onSearch={setQuery}
              placeholder="Number, instrument or model, e.g. aapl"
              label="Search backtests"
              maxLength={100}
              debounceMs={300}
            />
            {open && <List query={query} />}
          </div>
        </DialogBody>
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
