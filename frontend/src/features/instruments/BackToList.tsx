import { Link } from '@tanstack/react-router';
import { ArrowLeft } from 'lucide-react';

import { lastListSearch } from './lastSearch';

const LINK =
  'mb-4 inline-flex items-center gap-1.5 self-start rounded-md text-sm text-muted-foreground hover:text-foreground';

// "← Instruments" above an instrument: back to the list, with the search the user came from.
export function BackToList() {
  return (
    <Link to="/instruments" search={lastListSearch()} className={LINK}>
      <ArrowLeft className="size-4" />
      Instruments
    </Link>
  );
}
