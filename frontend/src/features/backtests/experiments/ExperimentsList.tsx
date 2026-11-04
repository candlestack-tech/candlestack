import { Link, useNavigate } from '@tanstack/react-router';
import { Globe, Search } from 'lucide-react';
import { useCallback, useEffect, type ReactNode } from 'react';

import type { ExperimentsPage } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { OutlineButton } from '@/components/DialogButtons';
import { Hint } from '@/components/Hint';
import { Pager } from '@/components/Pager';
import { SearchBox } from '@/components/SearchBox';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { isFiltered, withSortBy, withoutFilters, type ExperimentsSearch } from '@/routes/experimentsSearch';

import { ExperimentCard } from './ExperimentCard';
import { ExperimentChips, SortChip } from './ExperimentFilters';
import { ExperimentsTable } from './ExperimentsTable';
import { cardOf, experimentTarget } from './format';
import type { ExperimentsResult } from './useExperiments';
import type { ExperimentsView } from './view';

const NO_FACETS: ExperimentsPage['facets'] = { markets: [], instruments: [], representations: [], timeframes: [] };
// The API's page: four rows of three cards.
const PAGE_SIZE = 12;

// An old or shared link can point past the end of the table (?page=9 of 2): once the pages are
// known, the page goes to the last one, in place of the broken entry in the history.
function useClampPage(page: number, pages: number | undefined) {
  const navigate = useNavigate({ from: '/experiments' });
  useEffect(() => {
    if (pages === undefined || page <= pages) return;
    void navigate({ search: (prev) => ({ ...prev, page: pages > 1 ? pages : undefined }), replace: true });
  }, [page, pages, navigate]);
}

type ToolsProps = { search: ExperimentsSearch; first: ExperimentsPage | undefined; author: string | undefined };

// The search, the author chip, Market, Representation, Timeframe and, at the right, Sort
// (.bt-tools.ex-tools). On a phone the search takes its own line.
function Tools({ search, first, author }: ToolsProps) {
  const navigate = useNavigate({ from: '/experiments' });
  const onSearch = useCallback(
    (q: string) =>
      void navigate({ search: (prev) => ({ ...prev, q: q || undefined, page: undefined }), replace: true }),
    [navigate],
  );
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <SearchBox
        query={search.q ?? ''}
        onSearch={onSearch}
        placeholder="Name, instrument, model or author"
        label="Search experiments"
        maxLength={100}
        debounceMs={200}
        className="w-auto min-w-[180px] flex-[1_1_200px] sm:max-w-96 max-[640px]:basis-full"
      />
      <ExperimentChips search={search} facets={first?.facets ?? NO_FACETS} author={author} />
      <span className="flex-1 max-[640px]:hidden" />
      <SortChip search={search} />
    </div>
  );
}

const LINK = 'cursor-pointer text-[13px] font-medium text-primary underline-offset-2 hover:underline';

// The line over the list (.xc-count): who published how many, how many of yours are in, or how
// many of them match with the way back to all of them.
function CountLine({ search, first }: { search: ExperimentsSearch; first: ExperimentsPage }) {
  const navigate = useNavigate({ from: '/experiments' });
  const { counts, total } = first;
  const backtests = `${counts.all} ${counts.all === 1 ? 'backtest' : 'backtests'}`;
  let line: ReactNode = `${backtests} from ${counts.people} ${counts.people === 1 ? 'person' : 'people'}`;
  if (isFiltered(search)) {
    line = (
      <>
        <span>
          {total} of {search.mine ? counts.mine : counts.all}
        </span>
        <button type="button" className={LINK} onClick={() => void navigate({ search: withoutFilters })}>
          Clear filters
        </button>
      </>
    );
  } else if (search.mine) {
    line = `${counts.mine} of your backtests are in Experiments`;
  }
  return <p className="mb-3 flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">{line}</p>;
}

type EmptyProps = { icon: ReactNode; title: string; hint: string; action: ReactNode };

function EmptyCard({ icon, title, hint, action }: EmptyProps) {
  return (
    <section className="flex flex-col items-center gap-2 rounded-lg border bg-card px-4 py-11 text-center">
      <span className="mb-1 grid size-10 place-items-center rounded-[10px] bg-accent-soft text-primary">{icon}</span>
      <h2 className="text-[15px] font-semibold">{title}</h2>
      <Hint>{hint}</Hint>
      {action}
    </section>
  );
}

const SMALL = cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'mt-1 font-medium');

// Nothing to show (.ex-none): nothing of yours published yet, nothing published at all, or
// nothing that matches, with the way out of each.
function Empty({ search }: { search: ExperimentsSearch }) {
  const navigate = useNavigate({ from: '/experiments' });
  const toBacktests = (
    <Button asChild variant="outline" className={SMALL}>
      <Link to="/backtests">Go to Backtests</Link>
    </Button>
  );
  const hint = 'Open a finished backtest and click Publish. It shows up here and in All.';
  if (!isFiltered(search)) {
    const title = search.mine ? 'You have not published a backtest yet' : 'Nobody has published a backtest yet';
    return <EmptyCard icon={<Globe className="size-5" />} title={title} hint={hint} action={toBacktests} />;
  }
  return (
    <EmptyCard
      icon={<Search className="size-5" />}
      title={search.q ? `Nothing matches “${search.q}”` : 'Nothing matches these filters'}
      hint="Search by name, instrument, model or author, like Orion, NVDA, kagi or Tymur."
      action={
        <Button
          type="button"
          variant="outline"
          className={SMALL}
          onClick={() => void navigate({ search: withoutFilters })}
        >
          Clear search and filters
        </Button>
      }
    />
  );
}

// The cards, three a row at full width (272 px at least, 16 px apart), then Load more with how
// many are shown, or that they are all there (.xg, .xc-more).
function Cards({ result, total }: { result: ExperimentsResult; total: number }) {
  const { experiments, more } = result;
  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(272px,1fr))] gap-4">
        {experiments.map((experiment) => (
          <ExperimentCard key={experiment.id} facts={cardOf(experiment)} target={experimentTarget(experiment)} />
        ))}
      </div>
      {more.has ? (
        <div className="mt-5 flex flex-col items-center gap-2">
          <OutlineButton disabled={more.loading} onClick={more.load}>
            Load more
          </OutlineButton>
          <Hint>
            Showing {experiments.length} of {total}
          </Hint>
        </div>
      ) : (
        total > PAGE_SIZE && (
          <div className="mt-5 flex justify-center">
            <Hint>That is all {total}</Hint>
          </div>
        )
      )}
    </>
  );
}

// The table of the URL's page with the pager under it.
function TablePage({
  search,
  result,
  first,
}: {
  search: ExperimentsSearch;
  result: ExperimentsResult;
  first: ExperimentsPage;
}) {
  const navigate = useNavigate({ from: '/experiments' });
  const page = search.page ?? 1;
  return (
    <>
      <ExperimentsTable
        experiments={result.experiments}
        search={search}
        onSort={(column) => void navigate({ search: (prev) => withSortBy(prev, column) })}
      />
      <Pager
        from="/experiments"
        page={page}
        hasNext={page < first.pages}
        pages={first.pages}
        loading={result.loading}
      />
    </>
  );
}

type Props = { search: ExperimentsSearch; view: ExperimentsView; result: ExperimentsResult };

// Once the first page is in: why there is nothing, or the count over the cards or the table.
function Body({ search, view, result }: Props) {
  const { first } = result;
  if (!first) return null;
  if (result.experiments.length === 0) return <Empty search={search} />;
  return (
    <>
      <CountLine search={search} first={first} />
      {view === 'cards' ? (
        <Cards result={result} total={first.total} />
      ) : (
        <TablePage search={search} result={result} first={first} />
      )}
    </>
  );
}

// Experiments under its title (public-experiments, pageExp): the search, the chips and Sort, the
// count, then the cards with Load more or the table with the pager. Everything but the view is in
// the URL; the API searches, filters, sorts and pages. The author chip names the author from the
// backtests it finds.
export function ExperimentsList({ search, view, result }: Props) {
  const { first, experiments } = result;
  useClampPage(search.page ?? 1, view === 'table' && !result.loading ? first?.pages : undefined);
  const author = experiments.find((each) => each.author.id === search.author)?.author.name;
  return (
    <>
      <Tools search={search} first={first} author={author} />
      <Body search={search} view={view} result={result} />
    </>
  );
}
