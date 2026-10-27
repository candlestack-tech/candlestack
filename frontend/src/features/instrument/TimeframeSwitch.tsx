import { Link } from '@tanstack/react-router';

import type { Timeframe } from '@/api';
import { cn } from '@/lib/utils';

// Links, not buttons: the timeframe lives in the URL (?tf=1h), so the address opens the same view.
// The history state goes along.
// Solid, so the background grid does not show through them.
export function TimeframeSwitch({ timeframes, current }: { timeframes: readonly Timeframe[]; current: Timeframe }) {
  return (
    <nav aria-label="Timeframe" className="flex flex-wrap gap-1">
      {timeframes.map((tf) => (
        <Link
          key={tf}
          from="/instrument/$id"
          search={{ tf }}
          replace
          state={(prev) => prev}
          aria-current={tf === current ? 'page' : undefined}
          className={cn(
            'rounded-md border bg-background px-2.5 py-1 font-mono text-sm text-muted-foreground transition-colors hover:text-foreground sm:px-3',
            tf === current && 'border-primary text-primary hover:text-primary',
          )}
        >
          {tf}
        </Link>
      ))}
    </nav>
  );
}
