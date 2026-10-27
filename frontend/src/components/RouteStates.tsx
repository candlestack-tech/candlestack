import { Link, useRouter, type ErrorComponentProps } from '@tanstack/react-router';

import { ApiError } from '@/api';
import { PageTitle } from '@/components/PageTitle';
import { Button } from '@/components/ui/button';
import { usePageTitle } from '@/lib/usePageTitle';

// Shown while a route loads its data (a route loader, or the code of a lazy route).
export function RoutePending() {
  return <output className="block py-24 text-center text-sm text-muted-foreground">Loading…</output>;
}

function describe(error: unknown): { title: string; detail: string } {
  if (!(error instanceof ApiError)) {
    return { title: 'Something went wrong', detail: error instanceof Error ? error.message : String(error) };
  }
  if (error.status === 404) return { title: 'Not found', detail: error.message };
  const wait = error.retryAfter === undefined ? '' : ` Try again in ${error.retryAfter} s.`;
  return { title: 'Could not load this page', detail: `${error.message}${wait}` };
}

// Shown when a route loader fails, e.g. an unknown instrument (404) or the rate limit (429).
export function RouteError({ error }: ErrorComponentProps) {
  const router = useRouter();
  const { title, detail } = describe(error);
  usePageTitle(title);

  return (
    <>
      <PageTitle title={title} description={detail} />
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => void router.invalidate()}>
          Try again
        </Button>
        <Button asChild variant="ghost">
          <Link to="/">Back to home</Link>
        </Button>
      </div>
    </>
  );
}
