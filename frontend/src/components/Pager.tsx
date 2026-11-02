import { Link } from '@tanstack/react-router';

import { cn } from '@/lib/utils';

type Props = {
  // The list page whose ?page= the links change.
  from: '/instruments' | '/backtests' | '/experiments';
  page: number;
  hasNext: boolean;
  // How many pages there are; undefined when the API does not say.
  pages: number | undefined;
  loading: boolean;
};

const LINK = 'rounded-md border bg-background px-3 py-1.5 text-sm transition-colors hover:bg-accent';
const DISABLED = 'opacity-40';

type LinkProps = Pick<Props, 'from'> & { page: number | undefined; enabled: boolean; children: string };

// A link to another page, or a plain label when there is none: a disabled link would still be
// a link for the keyboard, screen readers and a middle click.
function PageLink({ from, page, enabled, children }: LinkProps) {
  if (!enabled) {
    return (
      <span aria-disabled="true" className={cn(LINK, DISABLED)}>
        {children}
      </span>
    );
  }
  return (
    <Link from={from} to="." search={(prev) => ({ ...prev, page })} className={LINK}>
      {children}
    </Link>
  );
}

// "Page 2 of 31" with previous and next (mockups/app, .pg), under the instrument list, the
// backtests and the table of Experiments. The page is in the URL, so Back steps through pages. While a page loads, the
// previous one stays on screen with its own `hasNext` and `pages`, so both links wait: two quick
// clicks on Next would otherwise skip a page or run past the end.
export function Pager({ from, page, hasNext, pages, loading }: Props) {
  if (page === 1 && !hasNext) return null;
  return (
    <nav aria-label="Pages" className="mt-4 flex items-center justify-between gap-3">
      <PageLink from={from} page={page > 2 ? page - 1 : undefined} enabled={page > 1 && !loading}>
        Previous
      </PageLink>
      <span className="text-sm text-muted-foreground">
        Page {page}
        {pages === undefined ? '' : ` of ${pages}`}
      </span>
      <PageLink from={from} page={page + 1} enabled={hasNext && !loading}>
        Next
      </PageLink>
    </nav>
  );
}
