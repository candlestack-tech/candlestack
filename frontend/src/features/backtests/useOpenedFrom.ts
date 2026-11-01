import { useRouterState } from '@tanstack/react-router';

// Where a backtest's page goes back to: the dashboard or the model's page it was opened from,
// else the Backtests page.
export function useOpenedFrom(): 'dashboard' | 'model' | undefined {
  return useRouterState({
    select: ({ location }) => {
      if (location.pathname.startsWith('/dashboard')) return 'dashboard';
      return location.pathname.startsWith('/models/') ? 'model' : undefined;
    },
  });
}
