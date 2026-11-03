import { Link } from '@tanstack/react-router';

import type { Dashboard, Experiment } from '@/api';
import { Hint } from '@/components/Hint';
import { ExperimentsIcon } from '@/components/icons';
import { MiniChart } from '@/components/MiniChart';
import {
  AuthorAvatar,
  BacktestName,
  CompareToggle,
  ReturnValue,
  RowOpener,
  descriptionOf,
  experimentTarget,
} from '@/features/backtests';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentDayInline } from '@/lib/dates';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { STARRED_ROW, Widget } from './widgets';

// A row as Starred backtests has it (.dd-sr): the author's initials beside both lines; the name,
// the tiny chart of the return and the return; under them what it ran on, what its model reads
// and the timeframe, and the Sharpe ratio. Who published it and when is the row's title
// (backtest-names, ddRow).
function Row({ experiment }: { experiment: Experiment }) {
  const style = useViewerDateStyle();
  const { author } = experiment;
  const when = formatRecentDayInline(new Date(experiment.published_at), style, undefined, true);
  return (
    <div
      title={`By ${author.name}, published ${when}`}
      className={cn(STARRED_ROW, 'grid-cols-[auto_minmax(0,1fr)_64px_86px_32px] gap-y-1')}
    >
      <span className="row-span-2 grid min-h-[22px] w-[22px] place-items-center self-start">
        <AuthorAvatar name={author.name} size="md" />
      </span>
      <span className="flex min-w-0 items-center text-[13px]">
        <RowOpener backtest={experiment}>
          <BacktestName name={experiment.name} className="font-medium" />
        </RowOpener>
      </span>
      {experiment.equity ? <MiniChart strategy={experiment.equity.strategy} /> : <span />}
      <span className="text-right font-mono text-sm/[1.5] font-semibold whitespace-nowrap">
        {experiment.total_return !== null && <ReturnValue value={experiment.total_return} />}
      </span>
      <span className="row-span-2 self-center">
        <CompareToggle target={experimentTarget(experiment)} />
      </span>
      <span className="col-span-2 min-w-0 truncate font-mono text-[11.5px]/[1.5] text-muted-foreground">
        {descriptionOf(experiment)}
      </span>
      <span className="text-right font-mono text-[11px]/[1.5] whitespace-nowrap text-muted-foreground">
        Sharpe {formatSharpe(experiment.sharpe)}
      </span>
    </div>
  );
}

// The four newest backtests published to the beta (public-experiments, expCard), then the way to
// all of them in Experiments.
export function NewExperiments({ experiments }: { experiments: Dashboard['experiments'] }) {
  return (
    <Widget icon={ExperimentsIcon} title="New in Experiments" aside={<Hint>from the beta</Hint>}>
      <div className="flex flex-col">
        {experiments.backtests.map((experiment) => (
          <Row key={experiment.id} experiment={experiment} />
        ))}
      </div>
      <Link
        to="/experiments"
        className="mt-1.5 flex w-full justify-between gap-2.5 border-t px-1.5 pt-2.5 text-[12.5px] font-medium text-muted-foreground transition-colors duration-150 outline-none hover:text-foreground focus-visible:text-foreground motion-reduce:transition-none"
      >
        <span>Open Experiments</span>
        <span>{experiments.total} published →</span>
      </Link>
    </Widget>
  );
}
