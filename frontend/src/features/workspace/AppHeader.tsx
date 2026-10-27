import { HeaderBar } from '@/components/HeaderBar';
import { ThemeToggle } from '@/components/ThemeToggle';
import { AccountMenu, useSignedInUser } from '@/features/account';

import { NewBacktest } from './NewBacktest';

// The signed-in pages' header (mockups/app): the logo to the dashboard and no nav, then New
// backtest, the theme toggle and the avatar with its menu. Instruments are in the sidebar, the
// API docs in the menu.
export function AppHeader() {
  const user = useSignedInUser();
  return (
    <HeaderBar home="/dashboard" homeLabel="CandleStack dashboard" app>
      <div className="ml-auto flex items-center gap-2">
        <NewBacktest />
        <ThemeToggle />
        <AccountMenu user={user} />
      </div>
    </HeaderBar>
  );
}
