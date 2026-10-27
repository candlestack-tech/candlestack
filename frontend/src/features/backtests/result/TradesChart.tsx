import { useEffect, useMemo, useRef, useState } from 'react';

import { exchangeLabel, type BacktestDocument } from '@/api';
import { HINT } from '@/components/Hint';
import { cn } from '@/lib/utils';

import { ChartBar, type Kind } from './ChartBar';
import { candleAt, marksIn, tradeAt, tradeView, valueAt, type Series, type TradeRow } from './chartData';
import { useChartNavigation } from './chartNavigation';
import { levelLabel } from './priceLevels';
import { ResultBox } from './ResultParts';
import { PriceLegend, ReturnsLegend, TradesNote } from './TradesLegend';

// "Price and trades" on a finished backtest's page (#309, the approved prototype): one chart of
// the price with the trades and the strategy against buy and hold under it, a legend that follows
// the cursor and a bar to pick the range, candles or a line and whether the trades show.

// A trade picked in the table; `count` makes the same trade picked again a new pick.
export type Picked = { index: number; count: number };

type Chart = Omit<ReturnType<typeof useChartNavigation>, 'container'>;

// Candles or a line, the trades shown or not, and which return lines show.
function useControls(view: Chart['view']) {
  const [kind, setKind] = useState<Kind>('candles');
  const [showTrades, setShowTrades] = useState(true);
  const [lines, setLines] = useState({ strategy: true, benchmark: true });
  useEffect(() => view?.setKind(kind === 'line'), [view, kind]);
  useEffect(() => view?.showTrades(showTrades), [view, showTrades]);
  useEffect(() => view?.setLines(lines), [view, lines]);
  return { kind, setKind, showTrades, setShowTrades, lines, setLines };
}

// The trade under the cursor, else the one picked in the table (the chart goes to it), and
// telling the page which one that is so the table marks its row.
function useHighlight(chart: Chart, showTrades: boolean, props: Props) {
  const { trades, picked, onHover } = props;
  const { candles, events, goTo, setRange, run } = chart;
  const [pinned, setPinned] = useState<number | null>(null);
  const card = useRef<HTMLElement>(null);
  const hover = useMemo(() => {
    if (!showTrades || !candles || events.cursor === null) return null;
    const index = candleAt(candles, events.cursor);
    const from = candles.times[index];
    if (from === undefined) return null;
    return tradeAt(trades, from, candles.times[index + 1] ?? candles.end)?.index ?? null;
  }, [showTrades, candles, events.cursor, trades]);
  useEffect(() => onHover(hover), [hover, onHover]);
  // A new pick only: the chart's own changes do not go back to the trade.
  const latest = useRef({ trades, goTo, setRange, run });
  useEffect(() => {
    latest.current = { trades, goTo, setRange, run };
  });
  useEffect(() => {
    const now = latest.current;
    const trade = picked && now.trades[picked.index];
    if (!trade) return;
    setPinned(trade.index);
    now.setRange(null);
    now.goTo(tradeView(trade, now.run.seconds, now.run.source.period));
    card.current?.scrollIntoView({ block: 'nearest' });
  }, [picked]);
  const highlighted = showTrades ? (hover ?? pinned) : null;
  return { highlighted, card, unpin: () => setPinned(null) };
}

// The arrows of the trades in view, the highlighted trade's link, and how many are in view.
function useTradeMarks(
  chart: Chart,
  controls: ReturnType<typeof useControls>,
  trades: readonly TradeRow[],
  highlighted: number | null,
) {
  const { view, candles, drawn, events } = chart;
  const { kind, showTrades } = controls;
  const marked = useMemo(
    () =>
      candles && events.visible ? marksIn(trades, events.visible, candles, highlighted) : { marks: [], inView: 0 },
    [candles, events.visible, trades, highlighted],
  );
  useEffect(() => {
    view?.setMarks(showTrades ? marked.marks : []);
  }, [view, drawn, kind, showTrades, marked]);
  useEffect(() => {
    const trade = highlighted === null ? null : (trades[highlighted] ?? null);
    const time = (seconds: number) => (candles ? candles.times[candleAt(candles, seconds)] : undefined);
    view?.setLink(trade, {
      open: trade ? time(trade.openTs) : undefined,
      close: trade ? time(trade.closeTs) : undefined,
    });
  }, [view, drawn, candles, trades, highlighted]);
  return marked.inView;
}

// The candle under the cursor, else the last.
function candleIndex({ candles, events }: Chart): number {
  if (!candles) return -1;
  const index = events.cursor === null ? -1 : candleAt(candles, events.cursor);
  return index >= 0 ? index : candles.times.length - 1;
}

// What the legends show: the candle under the cursor, else the last, and the returns at it.
function legendAt(chart: Chart) {
  const { frame, returns } = chart;
  const bars = frame?.bars ?? [];
  const at = candleIndex(chart);
  const value = (values: readonly (number | undefined)[] | null | undefined) =>
    values ? valueAt(values, at) : undefined;
  return {
    bar: bars[at],
    before: bars[at - 1],
    strategy: value(returns?.strategy),
    benchmark: value(returns?.benchmark),
    drawdown: value(returns?.drawdown),
  };
}

type Props = {
  doc: BacktestDocument;
  series: Series;
  trades: readonly TradeRow[];
  picked: Picked | null;
  onHover: (index: number | null) => void;
  // A range picked under the chart: the trade picked in the table no longer shows.
  onUnpick: () => void;
};

// The way to zoom, or why the candles are missing with a way to ask again.
function HeadNote({ prices }: { prices: Chart['prices'] }) {
  if (!prices.failed) return <span className={HINT}>Scroll to zoom, drag to move</span>;
  return (
    <span className="text-xs text-down">
      Candles did not load.{' '}
      <button type="button" className="underline underline-offset-2" onClick={prices.retry}>
        Try again
      </button>
    </span>
  );
}

type LegendsProps = {
  doc: BacktestDocument;
  chart: Chart;
  controls: ReturnType<typeof useControls>;
  trades: readonly TradeRow[];
  highlighted: number | null;
  inView: number;
};

function Legends({ doc, chart, controls, trades, highlighted, inView }: LegendsProps) {
  const legend = legendAt(chart);
  const { instrument } = doc.data;
  const exchange = instrument.exchange
    ? exchangeLabel(instrument.exchange)
    : instrument.source.charAt(0).toUpperCase() + instrument.source.slice(1);
  const line = (name: 'strategy' | 'benchmark', value: number | undefined) => ({
    value,
    on: controls.lines[name],
    onToggle: () => controls.setLines((lines) => ({ ...lines, [name]: !lines[name] })),
  });
  return (
    <>
      <PriceLegend
        title={`${instrument.symbol} · ${levelLabel(chart.level)} · ${exchange}`}
        bar={legend.bar}
        before={legend.before}
        decimals={chart.drawn?.decimals ?? 2}
        trades={
          <div className={cn(!controls.showTrades && 'invisible')}>
            <TradesNote
              trade={highlighted === null ? null : (trades[highlighted] ?? null)}
              inView={inView}
              style={chart.style}
              daily={chart.run.seconds >= 86_400}
            />
          </div>
        }
      />
      <ReturnsLegend
        top={chart.events.paneHeight + 5}
        strategy={line('strategy', legend.strategy)}
        benchmark={chart.returns?.benchmark ? line('benchmark', legend.benchmark) : null}
        drawdown={legend.drawdown}
      />
    </>
  );
}

export function TradesChart(props: Props) {
  const { doc, series, trades } = props;
  const { container, ...chart } = useChartNavigation(doc, series, trades);
  const controls = useControls(chart.view);
  const { highlighted, card, unpin } = useHighlight(chart, controls.showTrades, props);
  const inView = useTradeMarks(chart, controls, trades, highlighted);
  return (
    <ResultBox ref={card} title="Price and trades" aside={<HeadNote prices={chart.prices} />} className="pb-0">
      <div className="relative -mx-4 h-[420px]">
        <figure
          ref={container}
          aria-label={`${doc.data.instrument.symbol} price with the trades`}
          className="absolute inset-0 m-0"
        />
        <Legends {...{ doc, chart, controls, trades, highlighted, inView }} />
      </div>
      <div className="-mx-4">
        <ChartBar
          range={chart.range}
          onRange={(name) => {
            unpin();
            props.onUnpick();
            chart.pick(name);
          }}
          label={levelLabel(chart.level)}
          loading={chart.prices.loading}
          kind={controls.kind}
          onKind={controls.setKind}
          trades={controls.showTrades}
          onTrades={controls.setShowTrades}
        />
      </div>
    </ResultBox>
  );
}
