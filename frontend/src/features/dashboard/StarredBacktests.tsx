import type { Backtest, Dashboard } from '@/api';
import { Hint } from '@/components/Hint';
import { BacktestsIcon } from '@/components/icons';
import { MiniChart } from '@/components/MiniChart';
import {
  BacktestName,
  BacktestReturn,
  CompareToggle,
  PublicMark,
  RowOpener,
  RowStar,
  compareTarget,
  descriptionOf,
  hasResults,
} from '@/features/backtests';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { MoreRow, STARRED_ROW, Widget } from './widgets';

type RowProps = { backtest: Backtest; onStar: (backtest: Backtest) => void };

// Two lines (.dd-sr): the star beside both; the name, the tiny chart of the return and the return;
// under them what it ran on, what its model reads and the timeframe, and the Sharpe ratio
// (backtest-names, ddRow). A failed backtest has the Failed badge instead, without a chart.
function Row({ backtest, onStar }: RowProps) {
  const done = hasResults(backtest);
  return (
    <div className={cn(STARRED_ROW, 'grid-cols-[auto_minmax(0,1fr)_64px_86px_32px] gap-y-1')}>
      <span className="row-span-2 self-start">
        <RowStar starred label={`Unstar ${backtest.name}`} onToggle={() => onStar(backtest)} />
      </span>
      <span className="flex min-w-0 items-center gap-1.5 text-[13px]">
        <RowOpener backtest={backtest}>
          <BacktestName name={backtest.name} className="font-medium" />
        </RowOpener>
        {backtest.public && <PublicMark />}
      </span>
      {done && backtest.equity ? <MiniChart strategy={backtest.equity.strategy} /> : <span />}
      <span className="text-right font-mono text-sm/[1.5] font-semibold whitespace-nowrap">
        <BacktestReturn backtest={backtest} />
      </span>
      <span className="row-span-2 self-center">
        <CompareToggle target={compareTarget(backtest)} />
      </span>
      <span className="col-span-2 min-w-0 truncate font-mono text-[11.5px]/[1.5] text-muted-foreground">
        {descriptionOf(backtest)}
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
