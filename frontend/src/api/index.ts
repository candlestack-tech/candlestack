// The public face of src/api: pages and components import from '@/api' only.
export {
  connectUrl,
  deleteMe,
  deniedSignInQueryOptions,
  disconnectSignInMethod,
  meQueryOptions,
  signInUrl,
  signOut,
  signOutEverywhere,
  updateMe,
  type Me,
  type Provider,
  type SignInMethod,
} from './accounts';
export {
  backtestDownloadUrl,
  backtestQueryOptions,
  backtestsQueryOptions,
  dashboardQueryOptions,
  startBacktest,
  updateBacktest,
  type Backtest,
  type BacktestDocument,
  type BacktestIn,
  type BacktestStatus,
  type BacktestStep,
  type BacktestsPage,
  type BacktestsParams,
  type BestMetric,
  type Dashboard,
  type Representation,
  type SortOrder,
} from './backtests';
export { candleBridgeQueryOptions, candlePageQueryOptions, type Candles } from './candles';
export { ApiError, errorMessage } from './client';
export { EXCHANGES, exchangeLabel, exchangeMarket, type Exchange } from './exchanges';
export {
  exchangeName,
  instrumentIconUrl,
  instrumentQueryOptions,
  type InstrumentDetail,
  type Market,
} from './instruments';
export {
  preferencesQueryOptions,
  saveBacktestDefaults,
  updateAppearance,
  type Appearance,
  type BacktestDefaults,
  type BacktestDefaultsIn,
  type Benchmark,
  type DateFormat,
  type DefaultTimeframe,
  type Direction,
  type Preferences,
  type Theme,
  type TimeFormat,
  type TimeZone,
} from './preferences';
export {
  catalogQueryOptions,
  deleteModel,
  manifestQueryOptions,
  modelsQueryOptions,
  setManifest,
  updateModel,
  uploadModel,
  type Catalog,
  type CatalogBlock,
  type Manifest,
  type ManifestSummary,
  type Model,
  type ModelState,
  type Models,
  type StorageUsage,
} from './models';
export { queryClient } from './queryClient';
export {
  hasNextPage,
  instrumentSearchQueryOptions,
  resultTotal,
  type Instrument,
  type InstrumentSearchResult,
  type InstrumentSort,
} from './search';
export { TIMEFRAMES, instrumentTimeframe, parseTimeframe, type Timeframe } from './timeframes';
