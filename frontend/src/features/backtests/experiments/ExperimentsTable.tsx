import type { Experiment } from '@/api';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';
import { useViewerDateStyle } from '@/features/preferences';
import { formatDate, formatRecentDay, formatTime } from '@/lib/dates';
import { formatPercent, formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';
import type { ExperimentsColumn, ExperimentsSearch } from '@/routes/experimentsSearch';

import { CompareToggle } from '../compare/CompareToggle';
import { useComparedRow } from '../compare/selection';
import { instrumentId, instrumentList, symbolsOf } from '../format';
import { BacktestNameNumber, ModelName, ReturnValue, Missing } from '../parts';
import { CELL, COMPARE_CELL, HEAD, NUMBER, ROW, RowOpener, SortButton, TABLE } from '../table';
import { WIDTH } from '../widths';
import { AuthorAvatar } from './AuthorAvatar';
import { experimentTarget, readsText } from './format';

// The Backtests table with the author and what the model reads (public-experiments, eTable;
// backtest-names, .ex-t). A fixed layout as there: the columns keep their widths, cells 7 px from
// the side, and the backtest takes the rest, its name up to 160 px; the author's name and the
// model take up to two lines. From which width down each column hides, so the name keeps about
// 120 px or more: Max drawdown at 1200 px, Published at 852 and between 980 and 1080 (the sidebar
// beside the page leaves it narrow there), the model at 760, the author's name at 640 (the
// initials stay), Sharpe at 560, the instrument at 480. The results and Published sort.
type Column = { label: string; sort?: ExperimentsColumn | 'newest'; width?: string; hide?: string; number?: boolean };

const COLUMNS: readonly Column[] = [
  { label: 'Backtest' },
  { label: 'Author', width: 'w-[120px] max-[640px]:w-[52px]' },
  { label: 'Instrument', width: WIDTH.instruments, hide: 'max-[480px]:hidden' },
  { label: 'Model', width: 'w-[112px]', hide: 'max-[760px]:hidden' },
  { label: 'Return', sort: 'return', width: WIDTH.return, number: true },
  { label: 'Sharpe', sort: 'sharpe', width: WIDTH.sharpe, hide: 'max-[560px]:hidden', number: true },
  { label: 'Max drawdown', sort: 'max_drawdown', width: WIDTH.maxDrawdown, hide: 'max-[1200px]:hidden', number: true },
  {
    label: 'Published',
    sort: 'newest',
    width: 'w-[96px]',
    hide: 'max-[852px]:hidden min-[980px]:max-[1080px]:hidden',
  },
  { label: 'Compare', width: WIDTH.compare },
];
// Cells 7 px from the side (.ex-t th, .ex-t td).
const SIDES = 'px-[7px]';
const hideOf = (label: string) => COLUMNS.find((column) => column.label === label)?.hide;

type HeaderProps = { search: ExperimentsSearch; onSort: (column: ExperimentsColumn | undefined) => void };

// Which way a header sorts now: Published while nothing else does, newest first.
function sortOf(sort: Column['sort'], search: ExperimentsSearch): 'ascending' | 'descending' | undefined {
  if (sort === undefined) return undefined;
  if (sort === 'newest') return search.sort === undefined ? 'descending' : undefined;
  if (search.sort !== sort) return undefined;
  return search.order === 'asc' ? 'ascending' : 'descending';
}

function Header({ search, onSort }: HeaderProps) {
  return (
    <thead>
      <tr>
        {COLUMNS.map(({ label, sort, width, hide, number }) => {
          const order = sortOf(sort, search);
          const arrow = order === undefined ? '' : order === 'ascending' ? ' ↑' : ' ↓';
          return (
            <th key={label} scope="col" aria-sort={order} className={cn(HEAD, SIDES, width, hide, number && NUMBER)}>
              {sort ? (
                <SortButton on={order !== undefined} onClick={() => onSort(sort === 'newest' ? undefined : sort)}>
                  {label}
                  {arrow}
                </SortButton>
              ) : label === 'Compare' ? (
                <span className="sr-only">{label}</span>
              ) : (
                label
              )}
            </th>
          );
        })}
      </tr>
    </thead>
  );
}

// The author's initials and name, "you" after the user's own; the name takes up to two lines
// (.ex-t td.c-auth) and hides on a phone.
function AuthorCell({ author }: { author: Experiment['author'] }) {
  return (
    <span className="inline-flex max-w-full min-w-0 items-center gap-2">
      <AuthorAvatar name={author.name} size="md" />
      <span className="line-clamp-2 min-w-0 whitespace-normal max-[640px]:hidden">
        {author.name}
        {author.mine && <span className="ml-1 text-xs text-muted-foreground">you</span>}
      </span>
    </span>
  );
}

// The model by its mark (not a link: the file stays with its author), on up to two lines as in
// the Backtests table, what it reads under it.
function ModelCell({ experiment }: { experiment: Experiment }) {
  const reads = readsText(experiment);
  return (
    <span title={experiment.model.name} className="flex min-w-0 flex-col text-[12px]">
      <ModelName name={experiment.model.name} framework={experiment.model.framework} wrap />
      <span title={reads} className="truncate font-mono text-[11.5px] text-muted-foreground">
        {reads}
      </span>
    </span>
  );
}

function Row({ experiment }: { experiment: Experiment }) {
  const style = useViewerDateStyle();
  const compared = useComparedRow(experiment.id);
  const published = new Date(experiment.published_at);
  const [first] = experiment.instruments;
  return (
    <tr className={cn(ROW, compared)}>
      <td className={cn(CELL, SIDES)}>
        <RowOpener backtest={experiment}>
          <BacktestNameNumber backtest={experiment} max="max-w-[160px]" />
        </RowOpener>
      </td>
      <td className={cn(CELL, SIDES)}>
        <AuthorCell author={experiment.author} />
      </td>
      <td className={cn(CELL, SIDES, hideOf('Instrument'))}>
        {first && (
          <InstrumentSymbol id={instrumentId(first)} symbol={instrumentList(symbolsOf(experiment))} size={20} />
        )}
      </td>
      <td className={cn(CELL, SIDES, hideOf('Model'))}>
        <ModelCell experiment={experiment} />
      </td>
      <td className={cn(CELL, SIDES, NUMBER)}>
        {experiment.total_return === null ? <Missing /> : <ReturnValue value={experiment.total_return} />}
      </td>
      <td className={cn(CELL, SIDES, NUMBER, hideOf('Sharpe'))}>{formatSharpe(experiment.sharpe)}</td>
      <td className={cn(CELL, SIDES, NUMBER, hideOf('Max drawdown'))}>{formatPercent(experiment.max_drawdown)}</td>
      <td
        title={`${formatDate(published, style)} ${formatTime(published, style)}`}
        className={cn(CELL, SIDES, hideOf('Published'), 'text-muted-foreground')}
      >
        {formatRecentDay(published, style, undefined, true)}
      </td>
      <td className={cn(CELL, COMPARE_CELL)}>
        <CompareToggle target={experimentTarget(experiment)} />
      </td>
    </tr>
  );
}

type Props = HeaderProps & { experiments: readonly Experiment[] };

export function ExperimentsTable({ experiments, search, onSort }: Props) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <table className={cn(TABLE, 'table-fixed')}>
        <Header search={search} onSort={onSort} />
        <tbody>
          {experiments.map((experiment) => (
            <Row key={experiment.id} experiment={experiment} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
