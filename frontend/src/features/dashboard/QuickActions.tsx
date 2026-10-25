import { Plus } from 'lucide-react';

import { SwapIcon } from '@/components/icons';
import { QuickAction } from '@/components/QuickAction';
import { CompareDialog } from '@/features/backtests';
import { UploadCard, UploadDialog } from '@/features/models';

const LINE = 'text-[12.5px] text-muted-foreground';

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
      <CompareDialog>
        <QuickAction
          icon={SwapIcon}
          title="Compare backtests"
          line={<span className={LINE}>Two side by side on one chart</span>}
        />
      </CompareDialog>
    </div>
  );
}
