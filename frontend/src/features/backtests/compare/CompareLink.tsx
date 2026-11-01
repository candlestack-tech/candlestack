import { Link, useRouterState } from '@tanstack/react-router';
import type { ComponentProps } from 'react';

import { comparedFromOf } from './selection';

// A link to the compare page that remembers where it was opened.
export function CompareLink(props: Omit<ComponentProps<typeof Link>, 'to' | 'state'>) {
  const pathname = useRouterState({ select: ({ location }) => location.pathname });
  return <Link to="/backtests/compare" state={{ comparedFrom: comparedFromOf(pathname) }} {...props} />;
}
