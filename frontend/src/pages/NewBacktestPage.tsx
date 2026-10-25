import { Link, useRouterState, useSearch } from '@tanstack/react-router';

import { BacktestBuilder } from '@/features/backtests/builder';
import { usePageTitle } from '@/lib/usePageTitle';

const BACK =
  'mb-3.5 inline-flex items-center gap-1.5 text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground focus-visible:underline';

const BACK_TO = {
  dashboard: { to: '/dashboard', label: 'Dashboard' },
  backtests: { to: '/backtests', label: 'Backtests' },
  models: { to: '/models', label: 'Models' },
} as const;

// The page New backtest was opened from (its link's `from`), the dashboard otherwise.
function BackLink() {
  const from = useRouterState({ select: (state) => state.location.state.from });
  const { to, label } = from === 'backtests' || from === 'models' ? BACK_TO[from] : BACK_TO.dashboard;
  return (
    <Link to={to} className={BACK}>
      ← {label}
    </Link>
  );
}

// /backtests/new (#270): the builder, without the sidebar (mockups/app, pageBuilder).
export function NewBacktestPage() {
  const { model } = useSearch({ from: '/_app/backtests/new' });
  usePageTitle('New backtest');
  return (
    <>
      <BackLink />
      <BacktestBuilder modelId={model} />
    </>
  );
}
