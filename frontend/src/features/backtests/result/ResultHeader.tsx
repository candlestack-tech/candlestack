import { Link, useRouter, useRouterState } from '@tanstack/react-router';
import { Download, Globe } from 'lucide-react';
import { useState } from 'react';

import { getBacktestFile, type BacktestDocument } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';
import { Button } from '@/components/ui/button';
import { FrameworkTile, frameworkOf } from '@/features/models';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentDayInline, formatRecentTime, formatTime } from '@/lib/dates';
import { secondsSince, useNow } from '@/lib/useNow';
import { cn } from '@/lib/utils';

import { CompareButton } from '../compare/CompareToggle';
import { AuthorAvatar } from '../experiments/AuthorAvatar';
import { firstName } from '../experiments/format';
import { documentDescription, documentReads } from '../names';
import { RenameBacktest } from '../RenameBacktest';
import { BacktestTitle, StatusBadge } from '../parts';
import { durationText, periodText } from './format';
import { PublicPill, PublishActions } from './PublishActions';

// What every field of the downloaded file means, on the main website.
export const RESULT_DOCS = 'https://candlestack.tech/docs/backtest-json';

const BACK =
  'mb-3.5 inline-flex items-center gap-1.5 text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground focus-visible:underline';

// A way back through the history, to the page as it was left (its search and filters).
function BackButton({ children }: { children: string }) {
  const router = useRouter();
  return (
    <button
      type="button"
      onClick={() => {
        router.history.back();
      }}
      className={cn(BACK, 'cursor-pointer')}
    >
      ← {children}
    </button>
  );
}

// "← Backtests" above the page; opened from the dashboard "← Dashboard", from a model's page
// "← <the model>" (its links set `from` in the history state), from Experiments or the comparison
// back to them as they were. Another account's backtest opened from a link goes to Experiments.
export function BackLink({ model, others = false }: { model?: BacktestDocument['model']; others?: boolean }) {
  const from = useRouterState({ select: (state) => state.location.state.from });
  if (from === 'compare') return <BackButton>Compare backtests</BackButton>;
  if (from === 'experiments') return <BackButton>Experiments</BackButton>;
  if (from === 'dashboard') {
    return (
      <Link to="/dashboard" className={BACK}>
        ← Dashboard
      </Link>
    );
  }
  if (from === 'model' && model?.id) {
    return (
      <Link to="/models/$modelId" params={{ modelId: model.id }} className={BACK}>
        ← {model.name}
      </Link>
    );
  }
  return others ? (
    <Link to="/experiments" className={BACK}>
      ← Experiments
    </Link>
  ) : (
    <Link to="/backtests" className={BACK}>
      ← Backtests
    </Link>
  );
}

const SMALL = cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'font-medium');

// Saves the whole document as candlestack-backtest-<number>.json, a name the page gives it
// itself: some browsers drop the API's Content-Disposition name and save a bare id.
function useDownload(id: string, number: number) {
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const save = async () => {
    setSaving(true);
    setFailed(false);
    try {
      const url = URL.createObjectURL(await getBacktestFile(id));
      const link = document.createElement('a');
      link.href = url;
      link.download = `candlestack-backtest-${number}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };
  return { save, saving, failed };
}

type ActionProps = { id: string; number: number; others: boolean };

// Download JSON, and why it did not work when it failed. Another account's file holds what its
// page shows, never the model.
function DownloadButton({ id, number, others }: ActionProps) {
  const download = useDownload(id, number);
  return (
    <>
      {download.failed && (
        <span role="alert" className="mr-2 self-center text-[13px] text-down">
          The file did not download. Try again.
        </span>
      )}
      <Button
        type="button"
        variant="outline"
        title={others ? 'Results, trades and setup. No model file.' : undefined}
        className={cn(SMALL, 'rounded-r-none')}
        disabled={download.saving}
        onClick={() => void download.save()}
      >
        <Download aria-hidden="true" />
        Download JSON
      </Button>
    </>
  );
}

// [⤓ Download JSON | ?] as one group.
function FinishedActions(props: ActionProps) {
  return (
    <>
      <span className="inline-flex">
        <DownloadButton {...props} />
        <Button
          asChild
          variant="outline"
          className={cn(
            SMALL,
            'w-7 rounded-l-none border-l-0 px-0 font-mono text-muted-foreground hover:text-foreground',
          )}
        >
          <a
            href={RESULT_DOCS}
            target="_blank"
            rel="noopener"
            aria-label="About the JSON format"
            title="About the JSON format"
          >
            ?
          </a>
        </Button>
      </span>
    </>
  );
}

// The model it ran with beside its framework's mark, a link to its page while it exists. Another
// account's is its name only, in ink: the file stays with its author.
function ModelLink({ model }: { model: BacktestDocument['model'] }) {
  const framework = frameworkOf(model.framework);
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      {framework && <FrameworkTile framework={framework} size="sm" />}
      <ModelName model={model} />
    </span>
  );
}

function ModelName({ model }: { model: BacktestDocument['model'] }) {
  if (!model.id) return <span>{model.name}</span>;
  return (
    <Link
      to="/models/$modelId"
      params={{ modelId: model.id }}
      className="text-primary underline-offset-2 hover:underline"
    >
      {model.name}
    </Link>
  );
}

// The buttons: the user's own has Rename and Publish too; another account's only looks
// (public-experiments, pageBt).
function Actions({ doc }: { doc: BacktestDocument }) {
  const { backtest } = doc;
  const others = !backtest.author.mine;
  return (
    <div className="flex flex-wrap gap-2">
      {!others && <RenameBacktest backtest={backtest} />}
      {backtest.status === 'finished' && <FinishedActions id={backtest.id} number={backtest.number} others={others} />}
      {!others && <PublishActions doc={doc} />}
      <CompareButton
        target={{
          id: backtest.id,
          number: backtest.number,
          name: backtest.name,
          description: documentDescription(doc),
          status: backtest.status,
          symbol: doc.data.instrument.symbol,
          author: others ? backtest.author.name : undefined,
        }}
      />
    </div>
  );
}

// When it ran and how long, or for another account's when it was published; then since when the
// user's own is in Experiments, by a green globe.
function When({ backtest }: { backtest: BacktestDocument['backtest'] }) {
  const dateStyle = useViewerDateStyle();
  const { published_at: publishedAt } = backtest;
  const published = publishedAt === null ? null : new Date(publishedAt);
  if (!backtest.author.mine) {
    return <span>{published && `Published ${formatRecentDayInline(published, dateStyle, undefined, true)}`}</span>;
  }
  return (
    <>
      <span>
        {formatRecentTime(new Date(backtest.created_at), dateStyle)}
        {backtest.duration_s !== null && ` · ${durationText(backtest.duration_s)}`}
      </span>
      {backtest.public && published && (
        <span className="inline-flex items-center gap-[7px]">
          <Globe aria-hidden="true" className="size-3.5 text-up" />
          In Experiments since {formatRecentDayInline(published, dateStyle, undefined, true)}{' '}
          {formatTime(published, dateStyle)}
        </span>
      )}
    </>
  );
}

// What the number beside the title is: numbers are per account (backtest-names, .r7-id).
const numberTitle = ({ number, author }: BacktestDocument['backtest']) =>
  `Backtest number ${String(number)} in ${author.mine ? 'your' : `${firstName(author.name)}’s`} account`;

// The name on up to two lines, then on its first line the number, the status (with how long a
// running one runs) and the Public pill of the user's own published one (backtest-names, .r7-t).
function TitleRow({ backtest }: { backtest: BacktestDocument['backtest'] }) {
  const now = useNow(backtest.status === 'running');
  const runs =
    backtest.status === 'running'
      ? durationText(secondsSince(backtest.started_at ?? backtest.created_at, now) ?? 0)
      : undefined;
  return (
    <div className="flex min-w-0 flex-[1_1_420px] items-start gap-2.5">
      <BacktestTitle name={backtest.name} className="text-[22px]/7 font-semibold tracking-[-0.02em]" />
      <span title={numberTitle(backtest)} className="flex-none font-mono text-[13px]/7 text-muted-foreground">
        #{backtest.number}
      </span>
      <span className="mt-[5px] flex flex-none">
        <StatusBadge status={backtest.status} detail={runs} />
      </span>
      {backtest.author.mine && backtest.public && (
        <span className="mt-1 flex flex-none">
          <PublicPill />
        </span>
      )}
    </div>
  );
}

// The name (up to two lines) with the number, the status and the buttons, then what it ran on
// (the instrument with its icon, what its model reads and the timeframe), with what, over which
// days and when (mockups/app, runHead; backtest-names, .r7-t). A running backtest's status counts
// how long it runs. Published, a green Public pill follows the status; another account's starts
// the line under it with its author (public-experiments, pageBt).
export function ResultHeader({ doc }: { doc: BacktestDocument }) {
  const dateStyle = useViewerDateStyle();
  const { backtest } = doc;
  const reads = documentReads(doc);
  const others = !backtest.author.mine;
  return (
    <>
      <BackLink model={doc.model} others={others} />
      <div className="flex flex-wrap items-start justify-between gap-2.5">
        <TitleRow backtest={backtest} />
        <Actions doc={doc} />
      </div>
      <div className="mt-1 mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-muted-foreground">
        {others && (
          <span className="inline-flex items-center gap-[7px] font-medium text-foreground">
            <AuthorAvatar name={backtest.author.name} size="xs" />
            {backtest.author.name}
          </span>
        )}
        <span className="inline-flex min-w-0 items-center whitespace-pre">
          <InstrumentSymbol id={doc.data.instrument.id} symbol={doc.data.instrument.symbol} size={20} />
          {reads && ` · ${reads}`}
        </span>
        <span className={cn(others && 'text-foreground')}>
          <ModelLink model={doc.model} />
        </span>
        <span>{periodText(doc.data.period, dateStyle)}</span>
        <When backtest={backtest} />
      </div>
    </>
  );
}
