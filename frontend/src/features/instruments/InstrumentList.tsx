import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import {
  hasNextPage,
  instrumentSearchQueryOptions,
  resultTotal,
  type Exchange,
  type InstrumentSearchResult,
  type Market,
} from '@/api';
import { Pager } from '@/components/Pager';
import { Button } from '@/components/ui/button';

import type { ListSort } from '@/routes/instrumentsSearch';
import { cn } from '@/lib/utils';

import { mismatchOf, withExchange } from './filterRules';
import { InstrumentTable } from './InstrumentTable';
import { describeListError, emptyMessage, mismatchMessage, unavailableMessage, type ListError } from './messages';
import { clampedPage, lastPage, listBodyState } from './pagination';
import { apiSort, shownSort } from './sorting';

// Rows per page. The API allows up to 100; 25 fits about one screen.
export const LIST_PAGE_SIZE = 25;

type Props = {
  query: string;
  market: Market | undefined;
  exchange: Exchange | undefined;
  sort: ListSort | undefined;
  page: number;
};

function Note({ children, tone = 'muted' }: { children: string; tone?: 'muted' | 'error' }) {
  const color = tone === 'error' ? 'text-destructive' : 'text-muted-foreground';
  return <p className={`px-3 py-12 text-center text-sm ${color}`}>{children}</p>;
}

// What went wrong, what to do about it, and the server's words in small print. w-full: in the
// page's flex column (Layout.tsx) a centered box is otherwise as wide as its longest word.
function ErrorState({ error, onRetry }: { error: ListError; onRetry: () => void }) {
  return (
    <div role="alert" className="mx-auto flex w-full max-w-md flex-col items-center gap-2 py-12 text-center text-sm">
      <p className={error.tone === 'warn' ? 'font-medium text-warn' : 'font-medium text-destructive'}>{error.title}</p>
      <p className="text-muted-foreground">{error.hint}</p>
      {error.canRetry && (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          Try again
        </Button>
      )}
      {/* The server's words can hold a long path: it wraps inside the screen. */}
      {error.detail && (
        <p className="mt-3 max-w-full font-mono text-xs break-words text-muted-foreground/70">{error.detail}</p>
      )}
    </div>
  );
}

// An old or shared link can point past the end: /instruments?q=apple&page=40 with 3 pages. Once
// the total is known, the page goes to the last one, in place of the broken entry in the history.
// Returns whether that redirect is on its way.
function useClampPage(result: UseQueryResult<InstrumentSearchResult>, page: number): boolean {
  const navigate = useNavigate({ from: '/instruments' });
  const total = result.isPlaceholderData || !result.data ? undefined : resultTotal(result.data);
  const clamped = clampedPage(page, total, LIST_PAGE_SIZE);
  useEffect(() => {
    if (clamped === undefined) return;
    void navigate({ search: (prev) => ({ ...prev, page: clamped > 1 ? clamped : undefined }), replace: true });
  }, [clamped, navigate]);
  return clamped !== undefined;
}

// Without a total (the API before #109) the page cannot be clamped: say so, with a way back.
function PastTheEnd() {
  return (
    <p className="px-3 py-12 text-center text-sm text-muted-foreground">
      This page is past the end of the results.{' '}
      <Link from="/instruments" search={(prev) => ({ ...prev, page: undefined })} className="text-primary underline">
        Go to page 1
      </Link>
    </p>
  );
}

// An old or hand-edited link can pair a market with an exchange it does not trade on
// (?market=crypto&exchange=NYSE). Nothing can match, so the list says why, with a way out.
function Mismatch({ market, exchange }: { market: Market; exchange: Exchange }) {
  const { title, hint } = mismatchMessage(market, exchange);
  return (
    <div className="flex flex-col items-center gap-1.5 rounded-lg border bg-background px-3 py-10 text-center text-sm">
      <p className="font-medium">{title}</p>
      <p className="text-muted-foreground">{hint}</p>
      <Button asChild variant="outline" size="sm" className="mt-1.5">
        <Link from="/instruments" search={(prev) => withExchange(prev, undefined)}>
          Show all exchanges
        </Link>
      </Button>
    </div>
  );
}

type BodyProps = {
  items: InstrumentSearchResult['items'];
  sort: ListSort | undefined;
  waiting: boolean;
  page: number;
  empty: string;
};

// The rows, or why there are none. While the previous result stands in for the new one or the
// page is being clamped, an empty result is not the answer yet: it says "Loading", not "No
// instruments match" or "past the end" for a moment.
function ListBody({ items, sort, waiting, page, empty }: BodyProps) {
  const state = listBodyState(items.length, waiting, page);
  if (state === 'rows') return <InstrumentTable items={items} sort={sort} />;
  if (state === 'loading') return <Note>Loading instruments…</Note>;
  if (state === 'past') return <PastTheEnd />;
  return <Note>{empty}</Note>;
}

// The order of the rows on screen (shownSort): the last order that had its own result.
function useShownSort(sort: ListSort | undefined, placeholder: boolean): ListSort | undefined {
  const [previous, setPrevious] = useState(sort);
  if (!placeholder && previous !== sort) setPrevious(sort);
  return shownSort(sort, previous, placeholder);
}

// The API request of a page of the list.
function searchParams({ query, market, exchange, sort, page }: Props) {
  const offset = (page - 1) * LIST_PAGE_SIZE;
  return { q: query, market, exchange, sort: apiSort(sort), limit: LIST_PAGE_SIZE, offset };
}

// The search result for the query, market, exchange, order and page in the URL, with its empty and
// error states.
export function InstrumentList(props: Props) {
  const { query, market, exchange, sort, page } = props;
  const mismatch = mismatchOf(market, exchange);
  const result = useQuery({ ...instrumentSearchQueryOptions(searchParams(props)), enabled: !mismatch });
  const clamping = useClampPage(result, page);
  const shown = useShownSort(sort, result.isPlaceholderData);

  if (mismatch) return <Mismatch {...mismatch} />;
  if (result.isPending) return <Note>Loading instruments…</Note>;
  if (result.isError) {
    return <ErrorState error={describeListError(result.error, query)} onRetry={() => void result.refetch()} />;
  }

  const { data } = result;
  const total = resultTotal(data);
  const unavailable = unavailableMessage(data.unavailable);
  return (
    <>
      {unavailable && <p className="mb-3 rounded-md border bg-background px-3 py-2 text-sm text-warn">{unavailable}</p>}
      {/* Solid under the rows, also while they fade for the next page: no grid shows through. */}
      <div className="rounded-lg bg-background">
        <div className={cn('overflow-hidden rounded-lg border', result.isPlaceholderData && 'opacity-60')}>
          <ListBody
            items={data.items}
            sort={shown}
            waiting={result.isPlaceholderData || clamping}
            page={page}
            empty={emptyMessage(query, market, exchange)}
          />
        </div>
      </div>
      <Pager
        from="/instruments"
        page={page}
        hasNext={hasNextPage(data, LIST_PAGE_SIZE)}
        pages={total === undefined ? undefined : lastPage(total, LIST_PAGE_SIZE)}
        loading={result.isPlaceholderData}
      />
    </>
  );
}
