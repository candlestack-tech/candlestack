import type { InstrumentDetail, Timeframe } from '@/api';
import type { DateStyle } from '@/lib/dates';

import { CandleChart } from './CandleChart';
import { CandlesError } from './CandlesError';
import { timeZoneNote } from './time';
import { useCandleHistory, type CandleHistory } from './useCandleHistory';

// "1 candle is missing …" or "3 candles are missing …".
function gapsNote(count: number): string {
  const missing = count === 1 ? '1 candle is' : `${count} candles are`;
  return `${missing} missing in the source data and the chart skips ${count === 1 ? 'it' : 'them'}.`;
}

function Status({
  timeZone,
  history: { isLoadingOlder, hasOlder, gapsTotal },
}: {
  timeZone: string;
  history: CandleHistory;
}) {
  const notes = [
    isLoadingOlder ? 'Loading older candles…' : hasOlder ? 'Scroll left for older candles.' : 'All history is loaded.',
    timeZoneNote(timeZone),
  ];
  // The chart joins sessions, so a hole in the data would not show. Say so.
  if (gapsTotal > 0) notes.push(gapsNote(gapsTotal));
  return <p className="mt-2 text-xs text-muted-foreground">{notes.join(' ')}</p>;
}

type Props = { instrument: InstrumentDetail; timeframe: Timeframe; dateStyle: DateStyle };

function Placeholder({ children }: { children: string }) {
  return <p className="my-auto py-24 text-center text-sm text-muted-foreground">{children}</p>;
}

// The chart of one instrument at one timeframe, with its loading and error states. Without any
// candles there is no chart and no status line: a failed first load shows only its error. It
// fills the height the page leaves, for the chart, also while it loads or fails, so it does not
// jump when the candles arrive. It stops at 64rem, the chart at about 60rem with the status line:
// on a very tall screen the space left stays below it. The chart writes dates and times as the
// user does, in the user's zone.
export function InstrumentCandles({ instrument, timeframe, dateStyle }: Props) {
  const history = useCandleHistory(instrument, timeframe);
  const hasBars = history.bars.length > 0;

  return (
    <section
      aria-label={`${instrument.symbol} candles, ${timeframe}`}
      className="flex max-h-[64rem] flex-1 flex-col rounded-lg border bg-background p-2 sm:p-4"
    >
      {history.error && (
        <div className="mb-3">
          <CandlesError
            error={history.error}
            errorAt={history.errorAt}
            autoRetries={history.autoRetries}
            onRetry={history.retry}
            onAutoRetry={history.autoRetry}
          />
        </div>
      )}
      {history.isPending && <Placeholder>Loading candles…</Placeholder>}
      {!history.isPending && !hasBars && !history.error && <Placeholder>No candles in this period.</Placeholder>}
      {hasBars && (
        <>
          <CandleChart
            key={history.restarts}
            bars={history.bars}
            market={instrument.market}
            dateStyle={dateStyle}
            daily={timeframe === '1d'}
            onNearLeftEdge={history.loadOlder}
          />
          <Status timeZone={dateStyle.timeZone} history={history} />
        </>
      )}
    </section>
  );
}
