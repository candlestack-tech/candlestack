import { useNavigate, useRouterState } from '@tanstack/react-router';
import { Plus, Scale } from 'lucide-react';

import { QuickAction } from '@/components/QuickAction';
import { comparedFromOf, useCompareSelection } from '@/features/backtests';
import { UploadCard, UploadDialog } from '@/features/models';

const LINE = 'text-[12.5px] text-muted-foreground';

// Compare backtests opens the page, which says how to start when nothing is chosen.
function CompareAction() {
  const navigate = useNavigate();
  const { picks } = useCompareSelection();
  const pathname = useRouterState({ select: ({ location }) => location.pathname });
  return (
    <QuickAction
      icon={Scale}
      title="Compare backtests"
      line={<span className={LINE}>{picks.length > 0 ? `${picks.length} of 4 added` : 'Up to 4 side by side'}</span>}
      onClick={() => {
        void navigate({ to: '/backtests/compare', state: { comparedFrom: comparedFromOf(pathname) } });
      }}
    />
  );
}

// The strip under the greeting of an account with backtests (mockups/app, ddActions): New
// backtest on the accent's tint, Upload model with the frameworks' marks, Compare backtests. Three
// columns, one up to 760 px.
export function QuickActions({ onNewBacktest }: { onNewBacktest: () => void }) {
  return (
    <div className="grid grid-cols-3 gap-3 max-[760px]:grid-cols-1 max-[760px]:gap-2">
      <QuickAction
        icon={Plus}
        primary
        title="New backtest"
        line={<span className={LINE}>Pick the data, a model and a rule</span>}
        onClick={onNewBacktest}
      />
      <UploadDialog>
        <UploadCard />
      </UploadDialog>
      <CompareAction />
    </div>
  );
}
