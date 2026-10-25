import type { Backtest, Dashboard } from '@/api';
import { Hint } from '@/components/Hint';
import { BacktestsIcon } from '@/components/icons';
import {
  BacktestNumber,
  BacktestReturn,
  InstrumentChips,
  RowOpener,
  RowStar,
  hasResults,
  representationName,
} from '@/features/backtests';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { MoreRow, STARRED_ROW, Sparkline, Widget } from './widgets';

type RowProps = { backtest: Backtest; onStar: (backtest: Backtest) => void };

// Two lines (.dd-sb): the star beside both; the number with up to two instruments, the small
// equity line and the return; under them the representation and the model, and the Sharpe ratio.
// A failed backtest has the Failed badge instead, without a line.
function Row({ backtest, onStar }: RowProps) {
  const done = hasResults(backtest);
  return (
    <div className={cn(STARRED_ROW, 'grid-cols-[auto_minmax(0,1fr)_auto_auto] gap-y-px')}>
      <span className="row-span-2">
        <RowStar starred label={`Unstar backtest ${backtest.number}`} onToggle={() => onStar(backtest)} />
      </span>
      <span className="flex items-center gap-1.5 overflow-hidden text-[13px] text-ellipsis whitespace-nowrap">
        <RowOpener backtest={backtest}>
          <BacktestNumber backtest={backtest} />
        </RowOpener>
        <InstrumentChips backtest={backtest} max={2} tight />
      </span>
      {done && backtest.equity ? <Sparkline equity={backtest.equity} /> : <span />}
      <span className="text-right font-mono text-sm/[1.5] font-semibold whitespace-nowrap">
        <BacktestReturn backtest={backtest} />
      </span>
      <span className="col-span-2 truncate text-xs/[1.5] text-muted-foreground">
        {representationName(backtest.representation)} · {backtest.model.name}
      </span>
      <span className="text-right font-mono text-[11px]/[1.5] whitespace-nowrap text-muted-foreground">
        {done && `Sharpe ${formatSharpe(backtest.sharpe)}`}
      </span>
    </div>
  );
}

type Props = {
  starred: Dashboard['starred'];
  onStar: (backtest: Backtest) => void;
};

// The four starred backtests with the best Sharpe ratio (mockups/app, ddStarred), then More.
export function StarredBacktests({ starred, onStar }: Props) {
  return (
    <Widget icon={BacktestsIcon} title="Starred backtests" aside={<Hint>by Sharpe</Hint>}>
      {starred.backtests.length === 0 ? (
        <Hint>Star a backtest to keep it here.</Hint>
      ) : (
        <>
          <div className="flex flex-col">
            {starred.backtests.map((backtest) => (
              <Row key={backtest.id} backtest={backtest} onStar={onStar} />
            ))}
          </div>
          <MoreRow total={starred.total} to="/backtests" />
        </>
      )}
    </Widget>
  );
}
