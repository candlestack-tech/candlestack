import { Link, useRouterState } from '@tanstack/react-router';
import { Download } from 'lucide-react';

import { backtestDownloadUrl, type BacktestDocument } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';
import { Button } from '@/components/ui/button';
import { useViewerDateStyle } from '@/features/preferences';
import { formatRecentTime } from '@/lib/dates';
import { cn } from '@/lib/utils';

import { StatusBadge } from '../parts';
import { durationText, periodText } from './format';

// What every field of the downloaded file means, on the main website.
export const RESULT_DOCS = 'https://candlestack.tech/docs/backtest-json';

const BACK =
  'mb-3.5 inline-flex items-center gap-1.5 text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground focus-visible:underline';

// "← Backtests" above the page; opened from the dashboard "← Dashboard", from a model's page
// "← <the model>" (its links set `from` in the history state).
export function BackLink({ model }: { model?: BacktestDocument['model'] }) {
  const from = useRouterState({ select: (state) => state.location.state.from });
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
  return (
    <Link to="/backtests" className={BACK}>
      ← Backtests
    </Link>
  );
}

const SMALL = cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'font-medium');

// [⤓ Download JSON | ?] as one group, then Compare, which does nothing yet.
function FinishedActions({ id }: { id: string }) {
  return (
    <>
      <span className="inline-flex">
        <Button asChild variant="outline" className={cn(SMALL, 'rounded-r-none')}>
          <a href={backtestDownloadUrl(id)} download>
            <Download aria-hidden="true" />
            Download JSON
          </a>
        </Button>
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
      <Button type="button" variant="outline" className={SMALL}>
        Compare
      </Button>
    </>
  );
}

// The model it ran with, a link to its page while it exists.
function ModelLink({ model }: { model: BacktestDocument['model'] }) {
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

// The title with the status and the buttons, then what it ran on (the instrument with its icon),
// with what, over which days and when (mockups/app, runHead).
export function ResultHeader({ doc }: { doc: BacktestDocument }) {
  const dateStyle = useViewerDateStyle();
  const { backtest } = doc;
  const when = formatRecentTime(new Date(backtest.created_at), dateStyle);
  return (
    <>
      <BackLink model={doc.model} />
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex items-center gap-2.5">
          <h1 className="text-[22px]/7 font-semibold tracking-[-0.02em]">Backtest #{backtest.number}</h1>
          <StatusBadge status={backtest.status} />
        </div>
        {backtest.status === 'finished' && (
          <div className="flex flex-wrap gap-2">
            <FinishedActions id={backtest.id} />
          </div>
        )}
      </div>
      <div className="mt-1 mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-muted-foreground">
        <InstrumentSymbol id={doc.data.instrument.id} symbol={doc.data.instrument.symbol} size={20} />
        <ModelLink model={doc.model} />
        <span>{periodText(doc.data.period, dateStyle)}</span>
        <span>
          {when}
          {backtest.duration_s !== null && ` · ${durationText(backtest.duration_s)}`}
        </span>
      </div>
    </>
  );
}
