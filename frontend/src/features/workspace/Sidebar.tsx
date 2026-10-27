import { Link } from '@tanstack/react-router';
import { LayoutGrid } from 'lucide-react';

import { HINT } from '@/components/Hint';
import { BacktestsIcon, InstrumentsIcon, ModelsIcon, SettingsIcon } from '@/components/icons';
import { useDashboard } from '@/features/backtests';
import { SidebarStorage, useModels } from '@/features/models';

const SECTIONS = [
  { to: '/dashboard', label: 'Dashboard', Icon: LayoutGrid },
  { to: '/backtests', label: 'Backtests', Icon: BacktestsIcon },
  { to: '/models', label: 'Models', Icon: ModelsIcon },
  { to: '/instruments', label: 'Instruments', Icon: InstrumentsIcon },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon },
] as const;

// 34 px rows (the text's own line height, as .dd-nav): muted, ink on hover with a surface behind
// it; the open section ink and medium on a darker surface (the link marks it with data-status and
// aria-current). Hovering it changes nothing.
const ITEM =
  'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm/[normal] whitespace-nowrap text-muted-foreground transition-colors duration-150 hover:text-foreground hover:not-data-[status=active]:bg-muted data-[status=active]:bg-surface-2 data-[status=active]:font-medium data-[status=active]:text-foreground motion-reduce:transition-none max-[980px]:w-auto max-[980px]:flex-none';

// How many there are in a section, at the row's right (.dd-n): the backtests and the models, each
// once the API has said.
function useCounts(): Partial<Record<(typeof SECTIONS)[number]['to'], number>> {
  const backtests = useDashboard().data?.counts.all;
  const models = useModels().data?.models.length;
  return { '/backtests': backtests, '/models': models };
}

// The workspace's sections beside the page (mockups/app, direction D): a 200 px card, the storage
// and the data note under a line at its foot. Up to 980 px wide it lies above the page as a row
// that scrolls sideways, without a scrollbar and without the foot.
export function Sidebar() {
  const counts = useCounts();
  return (
    <nav
      aria-label="Workspace"
      className="flex flex-col gap-0.5 rounded-lg border bg-card p-2 max-[980px]:flex-row max-[980px]:overflow-x-auto max-[980px]:p-1 max-[980px]:[scrollbar-width:none] max-[980px]:[&::-webkit-scrollbar]:hidden"
    >
      {SECTIONS.map(({ to, label, Icon }) => (
        <Link key={to} to={to} className={ITEM}>
          <Icon className="size-4 shrink-0" />
          {label}
          {counts[to] !== undefined && (
            <span className="ml-auto pl-2 font-mono text-xs/[normal] font-normal text-muted-foreground">
              {counts[to]}
            </span>
          )}
        </Link>
      ))}
      <div className="mt-2.5 flex flex-col gap-1.5 border-t px-2.5 pt-3 pb-1 max-[980px]:hidden">
        <SidebarStorage />
        <p className={HINT}>Market data delayed 15 min</p>
      </div>
    </nav>
  );
}
