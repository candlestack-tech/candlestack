// The user's backtests: the Backtests page's list, the parts the dashboard draws them with, the
// comparison, the queries with the star, and a backtest's own page.
export { BacktestsList } from './BacktestsList';
export { ComparePanel } from './compare/ComparePanel';
export { CompareToggle } from './compare/CompareToggle';
export { lineOf } from './compare/chartLines';
export { SLOT_COLORS } from './compare/colors';
export { CompareCharts } from './compare/CompareCharts';
export { ComparePicker } from './compare/ComparePicker';
export { CompareTable, type CompareColumn } from './compare/CompareTable';
export {
  SLOTS,
  clearCompare,
  comparedFromOf,
  removeFromCompare,
  useComparedRow,
  useCompareSelection,
} from './compare/selection';
export { ModelBacktests } from './ModelBacktests';
export { BackLink, BacktestResult, useBacktestResult } from './result';
export { compareTarget, hasResults, representationName, returnClass } from './format';
export { BacktestInstruments, BacktestNumber, BacktestReturn, BacktestWhen, Missing, ModelName } from './parts';
export {
  BacktestCell,
  CELL,
  COMPARE_CELL,
  HEAD,
  NUMBER,
  ROW,
  ROW_OPENER,
  RowOpener,
  RowStar,
  SortButton,
  TABLE,
} from './table';
export { WIDTH } from './widths';
export { useBacktestsPage, useDashboard, useStarBacktest } from './useBacktests';
