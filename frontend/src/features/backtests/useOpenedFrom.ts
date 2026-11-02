import { useRouterState } from '@tanstack/react-router';

// Where a backtest's page goes back to: the dashboard, the model's page or Experiments it was
// opened from, else the Backtests page.
export function useOpenedFrom(): 'dashboard' | 'model' | 'experiments' | undefined {
  return useRouterState({
    select: ({ location }) => {
      if (location.pathname.startsWith('/dashboard')) return 'dashboard';
      if (location.pathname.startsWith('/experiments')) return 'experiments';
      return location.pathname.startsWith('/models/') ? 'model' : undefined;
    },
  });
}
