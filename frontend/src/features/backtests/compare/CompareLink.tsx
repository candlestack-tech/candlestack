import { useQueryClient } from '@tanstack/react-query';
import { Link, useRouterState } from '@tanstack/react-router';
import type { ComponentProps } from 'react';

import { backtestQueryOptions } from '@/api';

import { comparedFromOf } from './selection';

// A link to the compare page that remembers where it was opened; from a backtest's page, by the
// name of the backtest it shows.
export function CompareLink(props: Omit<ComponentProps<typeof Link>, 'to' | 'state'>) {
  const pathname = useRouterState({ select: ({ location }) => location.pathname });
  const queryClient = useQueryClient();
  const id = /^\/backtests\/([^/]+)$/.exec(pathname)?.[1];
  const name = id && queryClient.getQueryData(backtestQueryOptions(id).queryKey)?.backtest.name;
  return (
    <Link to="/backtests/compare" state={{ comparedFrom: comparedFromOf(pathname, name || undefined) }} {...props} />
  );
}
