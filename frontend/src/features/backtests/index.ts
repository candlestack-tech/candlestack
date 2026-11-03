// The user's backtests: the Backtests page's list, the parts the dashboard draws them with, the
// comparison, the queries with the star, a backtest's own page, and Experiments, the backtests
// published to the beta.
export { BacktestsList } from './BacktestsList';
export { AuthorAvatar } from './experiments/AuthorAvatar';
export { ExperimentsList } from './experiments/ExperimentsList';
export { ViewSwitch, WhoseSwitch } from './experiments/ExperimentsSwitches';
export { experimentTarget } from './experiments/format';
export { useExperiments } from './experiments/useExperiments';
export { useExperimentsView } from './experiments/view';
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
  pickName,
  removeFromCompare,
  useComparedRow,
  useCompareSelection,
  type ComparePick,
} from './compare/selection';
export { isMissing, useLeaveMissing, useNamesOf } from './compare/unpublished';
export { ModelBacktests } from './ModelBacktests';
export { BackLink, BacktestResult, useBacktestResult } from './result';
export { compareTarget, descriptionOf, hasResults, returnClass } from './format';
export {
  BacktestInstruments,
  BacktestName,
  BacktestNameNumber,
  BacktestReturn,
  BacktestWhen,
  Missing,
  PublicMark,
  ReturnValue,
} from './parts';
export {
  BacktestCell,
  CELL,
  COMPARE_CELL,
  HEAD,
  NAME_CELL,
  NAME_HEAD,
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
