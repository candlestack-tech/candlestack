import { Link } from '@tanstack/react-router';

import { ACCENT_BUTTON, BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// To the sign-in page, like the header's Try Backtest: the first sign-in creates the account.
export function TryBacktest() {
  return (
    <Button asChild className={cn(BUTTON_SIZES.lg, ACCENT_BUTTON)}>
      <Link to="/signin">Try Backtest</Link>
    </Button>
  );
}

const BROWSE = cn(BUTTON_SIZES.default, OUTLINE_BUTTON, 'mt-4');

// The instrument list is in the app (#340): signed out, the way there is the sign-in page, which
// returns to the list.
export function BrowseInstruments({ signedIn }: { signedIn: boolean }) {
  return (
    <Button asChild variant="outline" className={BROWSE}>
      {signedIn ? (
        <Link to="/instruments">Browse instruments</Link>
      ) : (
        <Link to="/signin" search={{ next: '/instruments' }}>
          Sign in to browse
        </Link>
      )}
    </Button>
  );
}
