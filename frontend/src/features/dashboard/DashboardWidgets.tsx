import { useNavigate } from '@tanstack/react-router';
import type { ReactNode } from 'react';

import type { Dashboard } from '@/api';
import { useStarBacktest } from '@/features/backtests';
import { useModels, useStarModel } from '@/features/models';
import { cn } from '@/lib/utils';

import { BestBacktests } from './BestBacktests';
import { GetStarted } from './GetStarted';
import { LastBacktests } from './LastBacktests';
import { QuickActions } from './QuickActions';
import { StarredBacktests } from './StarredBacktests';
import { showsStarredModels } from './order';
import { StarredModels } from './StarredModels';

// The two columns (.dd-cols): the backtests on the left, the starred ones in 356 px on the right;
// the widgets of both stacked up to 860 px. With one column of widgets, it takes the whole width.
function Columns({ left, right }: { left: ReactNode[]; right: ReactNode[] }) {
  if (left.length === 0 && right.length === 0) return null;
  const both = left.length > 0 && right.length > 0;
  const column = 'flex min-w-0 flex-col gap-4 max-[860px]:contents';
  return (
    <div
      className={cn(
        'grid items-start gap-4 max-[860px]:flex max-[860px]:flex-col max-[860px]:items-stretch',
        both ? 'grid-cols-[minmax(0,1fr)_minmax(0,356px)]' : 'grid-cols-[minmax(0,1fr)]',
      )}
    >
      {left.length > 0 && <div className={column}>{left}</div>}
      {right.length > 0 && <div className={column}>{right}</div>}
    </div>
  );
}

type Props = { dashboard: Dashboard };

// Everything under the greeting (mockups/app, ddDash). An account with backtests gets the quick
// actions, Last and Best backtests and both starred lists; a new one Get started, and a starred
// list once it has starred something.
export function DashboardWidgets({ dashboard }: Props) {
  const { data, error } = useModels();
  const models = data?.models;
  const navigate = useNavigate();
  const star = useStarBacktest();
  const starModel = useStarModel();
  const full = dashboard.counts.all > 0;
  const onStar = star.mutate;
  const openNew = () => void navigate({ to: '/backtests/new', state: { from: 'dashboard' } });
  const left = [
    full && <LastBacktests key="last" backtests={dashboard.last} onStar={onStar} />,
    dashboard.best.sharpe.length > 0 && <BestBacktests key="best" best={dashboard.best} />,
  ].filter(Boolean);
  const right = [
    full && <StarredBacktests key="runs" starred={dashboard.starred} onStar={onStar} />,
    showsStarredModels(models, error !== null, full) && (
      <StarredModels key="models" models={models} error={error} onStar={starModel.mutate} />
    ),
  ].filter(Boolean);
  return (
    <div className="flex flex-col gap-4">
      {full ? <QuickActions onNewBacktest={openNew} /> : <GetStarted onNewBacktest={openNew} />}
      <Columns left={left} right={right} />
    </div>
  );
}
