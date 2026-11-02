import { useNavigate } from '@tanstack/react-router';
import { LayoutGrid } from 'lucide-react';

import type { ExperimentsPage } from '@/api';
import { RowsIcon } from '@/components/icons';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import type { ExperimentsSearch } from '@/routes/experimentsSearch';

import { setExperimentsView, type ExperimentsView } from './view';

// The app's segmented control at 34 px, to sit beside the title (public-experiments, .seg).
const SEGMENT = 'inline-flex h-[34px] items-center gap-1.5 px-3 [&_svg]:size-[15px]';
const COUNT = 'font-mono text-xs font-normal text-muted-foreground';

type Props = { search: ExperimentsSearch; counts: ExperimentsPage['counts'] | undefined };

// All · Mine with their counts, once the API has said them. Like every change, a switch starts
// at page 1 and keeps the search, the filters and the order.
export function WhoseSwitch({ search, counts }: Props) {
  const navigate = useNavigate({ from: '/experiments' });
  return (
    <ToggleGroup
      aria-label="Whose"
      value={search.mine ? 'mine' : 'all'}
      onValueChange={(whose) =>
        void navigate({ search: (prev) => ({ ...prev, mine: whose === 'mine' ? true : undefined, page: undefined }) })
      }
    >
      <ToggleGroupItem value="all" className={SEGMENT}>
        All{counts && <span className={COUNT}>{counts.all}</span>}
      </ToggleGroupItem>
      <ToggleGroupItem value="mine" className={SEGMENT}>
        Mine{counts && <span className={COUNT}>{counts.mine}</span>}
      </ToggleGroupItem>
    </ToggleGroup>
  );
}

// Cards · Table, remembered in this browser (view.ts).
export function ViewSwitch({ view }: { view: ExperimentsView }) {
  return (
    <ToggleGroup aria-label="View" value={view} onValueChange={setExperimentsView}>
      <ToggleGroupItem value="cards" className={SEGMENT}>
        <LayoutGrid aria-hidden="true" />
        Cards
      </ToggleGroupItem>
      <ToggleGroupItem value="table" className={SEGMENT}>
        <RowsIcon />
        Table
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
