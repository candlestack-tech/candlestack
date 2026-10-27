// The instrument page's parts; src/pages/InstrumentPage.tsx puts them together. The backtest page
// writes its charts' times and reads its candles as the candle chart does.
export { InstrumentCandles } from './InstrumentCandles';
export { InstrumentHeader } from './InstrumentHeader';
export { TimeframeSwitch } from './TimeframeSwitch';
export { crosshairFormatter, dayFormatter, tickFormatter } from './time';
export { toBars, type Bar } from './bars';
