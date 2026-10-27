import { useCallback, useMemo, useState } from 'react';

import type { BacktestDocument } from '@/api';
import { FactsStrip } from '@/components/FactsStrip';

import { chartTrades } from './chartData';
import { allMetrics, basicMetrics, TONE } from './format';
import { FailedNote, MetricList, ResultBox, SetupBox } from './ResultParts';
import { ResultHeader } from './ResultHeader';
import { RunSteps } from './RunSteps';
import { TradesBox } from './TradesBox';
import { TradesChart, type Picked } from './TradesChart';

const DAY = 86_400;

type Results = NonNullable<BacktestDocument['results']>;
type Props = { doc: BacktestDocument; results: Results };

// The chart of the price and the trades over the strategy's return, then the trades. The two
// point at the same trade: the one under the chart's cursor is marked in the table, a row clicked
// shows its trade on the chart.
function Charts({ doc, results }: Props) {
  const trades = useMemo(() => chartTrades(results.trades), [results.trades]);
  const [hovered, setHovered] = useState<number | null>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const onPick = useCallback((index: number) => setPicked((last) => ({ index, count: (last?.count ?? 0) + 1 })), []);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <TradesChart doc={doc} series={results.series} trades={trades} picked={picked} onHover={setHovered} />
      <TradesBox
        trades={results.trades}
        longOnly={doc.decision.long_only}
        daily={doc.data.timeframe_seconds >= DAY}
        highlighted={hovered ?? picked?.index ?? null}
        onPick={onPick}
      />
    </div>
  );
}

// A finished run (mockups/app, resA): the first results in a strip, then the charts and the
// trades beside every metric and the setup; one column up to 980 px.
function Finished({ doc, results }: Props) {
  const facts = basicMetrics(results.stats).map(({ name, value, tone }) => ({
    term: name,
    value,
    className: tone && TONE[tone],
  }));
  return (
    <>
      <FactsStrip facts={facts} />
      <div className="grid grid-cols-[minmax(0,1fr)_280px] items-start gap-4 max-[980px]:grid-cols-[minmax(0,1fr)]">
        <Charts doc={doc} results={results} />
        <aside className="flex min-w-0 flex-col gap-4">
          <ResultBox title="All metrics">
            <MetricList metrics={allMetrics(results.stats)} />
          </ResultBox>
          <SetupBox doc={doc} />
        </aside>
      </div>
    </>
  );
}

// One backtest's page by its status: the results once it finished, its steps while it runs and
// once it failed (only the error when it failed before steps were recorded); the setup with
// either of the last two.
export function BacktestResult({ doc }: { doc: BacktestDocument }) {
  const { backtest, results } = doc;
  return (
    <>
      <ResultHeader doc={doc} />
      {backtest.status === 'finished' && results ? (
        <Finished doc={doc} results={results} />
      ) : (
        <div className="flex flex-col gap-4">
          {backtest.status === 'running' || backtest.steps.some((step) => step.state === 'failed') ? (
            <RunSteps doc={doc} />
          ) : (
            <FailedNote error={backtest.error ?? 'The backtest failed.'} />
          )}
          <SetupBox doc={doc} />
        </div>
      )}
    </>
  );
}
