import { Link } from '@tanstack/react-router';
import { Link as LinkIcon } from 'lucide-react';
import type { ReactNode } from 'react';

import type { AppClient } from '@/api';
import { LogoMark } from '@/brand/LogoMark';
import { AppTile } from '@/components/AppTile';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { ClockIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// The parts of the consent page (design/mockups/api-mcp, pageConsent): the card, the app and
// CandleStack linked, the titles, and the short state cards.

export const TITLE = 'text-[22px]/7 font-semibold tracking-[-0.02em]';
export const LEAD = 'mt-1.5 text-muted-foreground [&_b]:font-medium [&_b]:text-foreground';

// .one: the one card in the middle of the grid, 440 px at most, rising 8 px into place as it
// fades in like the sign-in card. `center` (.one.mid) centers a short state card.
export function ConsentFrame({ center = false, children }: { center?: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        'flex w-[min(440px,100%)] animate-in flex-col gap-[18px] rounded-xl border bg-background px-7 pt-7 pb-6 shadow-[0_10px_30px_var(--shadow)] duration-220 ease-out fade-in slide-in-from-bottom-2 motion-reduce:animate-none max-sm:px-5',
        center && 'items-center text-center',
      )}
    >
      {children}
    </div>
  );
}

function Dots() {
  return (
    <span className="flex gap-1 text-muted-foreground">
      <i className="size-1 rounded-full bg-current opacity-60" />
      <i className="size-1 rounded-full bg-current opacity-60" />
    </span>
  );
}

// .pair: the app's tile, a link between dots, the CandleStack mark.
export function AppPair({ client }: { client: AppClient }) {
  return (
    <div className="flex items-center justify-center gap-2.5">
      <AppTile client={client} className="size-11 rounded-[10px] text-[19px]" markClassName="size-[26px]" />
      <Dots />
      <span className="grid size-[26px] place-items-center rounded-full border bg-background text-muted-foreground">
        <LinkIcon aria-hidden="true" className="size-[13px]" />
      </span>
      <Dots />
      <LogoMark className="size-11" />
    </div>
  );
}

// The buttons of a state card, side by side in the middle.
export function StateActions({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap justify-center gap-2.5">{children}</div>;
}

// A request that was decided, expired (10 minutes) or never was, and an address without one.
export function ExpiredRequest() {
  return (
    <ConsentFrame center>
      <span className="grid size-11 place-items-center rounded-xl bg-[color-mix(in_srgb,var(--warn)_11%,var(--background))] text-warn">
        <ClockIcon className="size-[21px]" />
      </span>
      <div>
        <h1 className={TITLE}>This request has expired</h1>
        <p className={LEAD}>
          A request to connect an app works once, for 10 minutes. Go back to the app and connect again.
        </p>
      </div>
      <StateActions>
        <Button asChild variant="outline" className={cn(BUTTON_SIZES.default, OUTLINE_BUTTON)}>
          <Link to="/dashboard">Go to Dashboard</Link>
        </Button>
      </StateActions>
    </ConsentFrame>
  );
}

// The request or the session could not be read (no answer, a server error): why, and Try again.
export function LoadFailed({
  message,
  retrying,
  onRetry,
}: {
  message: string;
  retrying: boolean;
  onRetry: () => void;
}) {
  return (
    <ConsentFrame center>
      <LogoMark className="size-11" />
      <div>
        <h1 className={TITLE}>Could not load this request</h1>
        <p role="alert" className={LEAD}>
          {message}
        </p>
      </div>
      <StateActions>
        <Button
          type="button"
          variant="outline"
          className={cn(BUTTON_SIZES.default, OUTLINE_BUTTON)}
          disabled={retrying}
          onClick={onRetry}
        >
          Try again
        </Button>
      </StateActions>
    </ConsentFrame>
  );
}
