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
import { BacktestNumber, ModelName, ReturnValue, Missing } from '../parts';
import { CELL, COMPARE_CELL, HEAD, NUMBER, ROW, RowOpener, SortButton, TABLE } from '../table';
import { WIDTH } from '../widths';
import { AuthorAvatar } from './AuthorAvatar';
import { experimentTarget, readsText } from './format';

// The Backtests table with the author and what the model reads (public-experiments, eTable). A
// fixed layout as there: the columns keep their widths and the model takes the rest. Max drawdown
// hides at 1150 px as on the Backtests page, Published at 800, the model at 620, the author's name
// at 640 (the initials stay), Sharpe at 420. The results and Published sort.
type Column = { label: string; sort?: ExperimentsColumn | 'newest'; width?: string; hide?: string; number?: boolean };

const COLUMNS: readonly Column[] = [
  { label: 'Backtest', width: 'w-[72px]' },
  { label: 'Author', width: 'w-[168px] max-[640px]:w-[52px]' },
  { label: 'Instrument', width: WIDTH.instruments },
  { label: 'Model', hide: 'max-[620px]:hidden' },
  { label: 'Return', sort: 'return', width: WIDTH.return, number: true },
  { label: 'Sharpe', sort: 'sharpe', width: WIDTH.sharpe, hide: 'max-[420px]:hidden', number: true },
  { label: 'Max drawdown', sort: 'max_drawdown', width: WIDTH.maxDrawdown, hide: 'max-[1150px]:hidden', number: true },
  { label: 'Published', sort: 'newest', width: 'w-[104px]', hide: 'max-[800px]:hidden' },
  { label: 'Compare', width: WIDTH.compare },
];
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
            <th key={label} scope="col" aria-sort={order} className={cn(HEAD, width, hide, number && NUMBER)}>
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

// The author's initials and name, "you" after the user's own; the name hides on a phone.
function AuthorCell({ author }: { author: Experiment['author'] }) {
  return (
    <span className="inline-flex max-w-full min-w-0 items-center gap-2">
      <AuthorAvatar name={author.name} size="md" />
      <span className="min-w-0 truncate max-[640px]:hidden">
        {author.name}
        {author.mine && <span className="ml-1 text-xs text-muted-foreground">you</span>}
      </span>
    </span>
  );
}

// The model by its mark (not a link: the file stays with its author), what it reads under it.
function ModelCell({ experiment }: { experiment: Experiment }) {
  const reads = readsText(experiment);
  return (
    <span title={experiment.model.name} className="flex min-w-0 flex-col text-[12px]">
      <ModelName name={experiment.model.name} framework={experiment.model.framework} />
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
      <td className={CELL}>
        <RowOpener backtest={experiment}>
          <BacktestNumber backtest={experiment} />
        </RowOpener>
      </td>
      <td className={CELL}>
        <AuthorCell author={experiment.author} />
      </td>
      <td className={CELL}>
        {first && (
          <InstrumentSymbol id={instrumentId(first)} symbol={instrumentList(symbolsOf(experiment))} size={20} />
        )}
      </td>
      <td className={cn(CELL, hideOf('Model'))}>
        <ModelCell experiment={experiment} />
      </td>
      <td className={cn(CELL, NUMBER)}>
        {experiment.total_return === null ? <Missing /> : <ReturnValue value={experiment.total_return} />}
      </td>
      <td className={cn(CELL, NUMBER, hideOf('Sharpe'))}>{formatSharpe(experiment.sharpe)}</td>
      <td className={cn(CELL, NUMBER, hideOf('Max drawdown'))}>{formatPercent(experiment.max_drawdown)}</td>
      <td
        title={`${formatDate(published, style)} ${formatTime(published, style)}`}
        className={cn(CELL, hideOf('Published'), 'text-muted-foreground')}
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
