import { Link, useRouterState } from '@tanstack/react-router';
import { Plus } from 'lucide-react';

import { ACCENT_BUTTON, BUTTON_SIZES } from '@/components/buttonStyles';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// Where the builder's back link returns: the part of the app New backtest was clicked in.
function fromOf(path: string) {
  if (path.startsWith('/models')) return 'models';
  if (path.startsWith('/backtests')) return 'backtests';
  return 'dashboard';
}

// The app header's New backtest, a link to the builder. On a phone only its plus shows, in a
// 34 px square; the plus is still a child there from sm up, so the button's own padding for an
// icon is set back to the text's.
export function NewBacktest() {
  const path = useRouterState({ select: (state) => state.location.pathname });
  return (
    <Button
      asChild
      className={cn(
        BUTTON_SIZES.default,
        ACCENT_BUTTON,
        'has-[>svg]:px-3.5 max-sm:w-[34px] max-sm:px-0 max-sm:has-[>svg]:px-0',
      )}
    >
      <Link to="/backtests/new" state={{ from: fromOf(path) }} aria-label="New backtest">
        <Plus className="sm:hidden" />
        <span className="max-sm:hidden">New backtest</span>
      </Link>
    </Button>
  );
}
