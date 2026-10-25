import { Outlet, useRouterState } from '@tanstack/react-router';

import { PageFrame } from '@/components/Layout';
import { SignedInUserContext, useMe } from '@/features/account';

import { AppHeader } from './AppHeader';
import { Sidebar } from './Sidebar';

// The sidebar beside the page, which fades up 6 px whenever another one opens (keyed by its
// route, so only the page that just appeared moves; another section of Settings is the same
// page, which moves only the section). Up to 980 px the sidebar lies above it. A page that says
// `sidebar: false` (the builder) takes the whole width.
function Workspace() {
  const page = useRouterState({ select: (state) => state.matches.at(-1)?.routeId });
  const bare = useRouterState({ select: (state) => state.matches.at(-1)?.staticData.sidebar === false });
  const view = (
    <div
      key={page}
      className="min-w-0 animate-in duration-200 ease-out fade-in slide-in-from-bottom-[6px] motion-reduce:animate-none"
    >
      <Outlet />
    </div>
  );
  if (bare) return <div className="text-sm/[1.5]">{view}</div>;
  return (
    <div className="grid grid-cols-[200px_minmax(0,1fr)] items-start gap-6 text-sm/[1.5] max-[980px]:grid-cols-[minmax(0,1fr)] max-[980px]:gap-4">
      <Sidebar />
      {view}
    </div>
  );
}

// The signed-in pages (mockups/app): the app header, the workspace and the footer. The route's
// guard has loaded the user. Signing out forgets it before leaving for the home page: nothing to
// show for that moment.
export function AppLayout() {
  const user = useMe();
  if (!user) return null;
  return (
    <SignedInUserContext value={user}>
      <PageFrame header={<AppHeader />} app>
        <Workspace />
      </PageFrame>
    </SignedInUserContext>
  );
}
