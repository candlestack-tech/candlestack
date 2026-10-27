import { Link, useRouterState } from '@tanstack/react-router';

import { ACCENT_BUTTON, BUTTON_SIZES, GHOST_BUTTON } from '@/components/buttonStyles';
import { HeaderBar } from '@/components/HeaderBar';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Button } from '@/components/ui/button';
import { AccountMenu, useMe } from '@/features/account';
import { cn } from '@/lib/utils';

type Section = 'product' | 'instruments';

// The nav item a page belongs to: an instrument is part of Instruments, though not the list itself.
function sectionOf(pathname: string): Section | undefined {
  if (pathname === '/') return 'product';
  if (pathname === '/instruments' || pathname.startsWith('/instrument/')) return 'instruments';
  return undefined;
}

const NAV = 'text-muted-foreground hover:text-foreground';
const ON = 'font-medium text-foreground';

// No room on a phone: the logo leads home. Instruments only signed in: the list is in the app,
// market data being for signed-in accounts only (#340).
function MainNav() {
  const section = useRouterState({ select: (state) => sectionOf(state.location.pathname) });
  const signedIn = Boolean(useMe());
  return (
    <nav aria-label="Main" className="flex gap-[18px] text-sm max-sm:hidden">
      <Link to="/" className={cn(NAV, section === 'product' && ON)}>
        Product
      </Link>
      {signedIn && (
        <Link
          to="/instruments"
          activeOptions={{ includeSearch: false }}
          className={cn(NAV, section === 'instruments' && ON)}
        >
          Instruments
        </Link>
      )}
      {/* Same origin, so stage links to the stage API. */}
      <a href="/api/v1/docs" target="_blank" rel="noopener" className={NAV}>
        API
      </a>
    </nav>
  );
}

// Signed out: Sign in and Try Backtest, both to the sign-in page (the first sign-in creates the
// account), which leaves them out. Sign in waits for md: with the nav, the row needs about 675 px.
// Signed in: Dashboard and the avatar with its menu.
function Actions() {
  const me = useMe();
  const onSignIn = useRouterState({ select: (state) => state.location.pathname === '/signin' });
  return (
    <div className="ml-auto flex items-center gap-2">
      {me ? (
        <Button asChild variant="ghost" className={cn(BUTTON_SIZES.default, GHOST_BUTTON)}>
          <Link to="/dashboard">Dashboard</Link>
        </Button>
      ) : (
        !onSignIn && (
          <>
            <Button asChild variant="ghost" className={cn(BUTTON_SIZES.default, GHOST_BUTTON, 'max-md:hidden')}>
              <Link to="/signin">Sign in</Link>
            </Button>
            <Button asChild className={cn(BUTTON_SIZES.default, ACCENT_BUTTON)}>
              <Link to="/signin">Try Backtest</Link>
            </Button>
          </>
        )
      )}
      <ThemeToggle />
      {me && <AccountMenu user={me} />}
    </div>
  );
}

// The public pages' header: Product, Instruments signed in, and the API docs.
export function Header() {
  return (
    <HeaderBar home="/" homeLabel="CandleStack home">
      <MainNav />
      <Actions />
    </HeaderBar>
  );
}
