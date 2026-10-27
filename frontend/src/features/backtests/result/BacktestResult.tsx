import type { BacktestDocument } from '@/api';
import { FactsStrip } from '@/components/FactsStrip';
import { InstrumentSymbol } from '@/components/InstrumentSymbol';
import { useDateStyle } from '@/features/preferences';

import { allMetrics, basicMetrics, TONE } from './format';
import { showsMarkers } from './chartData';
import { EquityCharts, PriceChart } from './ResultCharts';
import { FailedNote, Legend, MetricList, ResultBox, SetupBox } from './ResultParts';
import { ResultHeader } from './ResultHeader';
import { RunSteps } from './RunSteps';
import { TradesBox } from './TradesBox';

const DAY = 86_400;

type Results = NonNullable<BacktestDocument['results']>;
type Props = { doc: BacktestDocument; results: Results };

// The charts and the trades: the equity with the drawdown, the price with the trades on it (the box
// named by the instrument with its icon).
function Charts({ doc, results }: Props) {
  const dateStyle = useDateStyle();
  const { series, trades } = results;
  const daily = doc.data.timeframe_seconds >= DAY;
  const { instrument } = doc.data;
  const symbol = instrument.symbol;
  // A run of days, or the light view's one point per day: no time of day on the axis.
  const time = { dateStyle, daily: daily || series.resolution === 'day' };
  const lines =
    doc.benchmarks.length > 0
      ? ([
          ['line', 'Strategy'],
          ['dashed', 'Buy and hold'],
        ] as const)
      : ([['line', 'Strategy']] as const);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ResultBox title="Equity" aside={<Legend items={lines} />}>
        <EquityCharts series={series} time={time} />
      </ResultBox>
      <ResultBox
        title={<InstrumentSymbol id={instrument.id} symbol={symbol} size={16} />}
        aside={
          showsMarkers(trades) && (
            <Legend
              items={[
                ['up', 'Buy'],
                ['down', 'Sell'],
              ]}
            />
          )
        }
      >
        <PriceChart series={series} trades={trades} time={time} symbol={symbol} />
      </ResultBox>
      <TradesBox trades={trades} longOnly={doc.decision.long_only} daily={daily} />
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
