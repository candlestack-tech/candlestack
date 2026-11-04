// The widths of the columns the tables of backtests share (#315), padding included: room for the
// longest value and for the header with its sort arrow in the app's fonts, so neither is ever cut.
// Return fits "+9,999.9 %", When "12 Oct 22:34" on one line (an older run's "28 May 2025
// 11:58 AM" takes two), the instruments one symbol of seven letters with its icon (a "+N" after it
// cuts the symbol). The Backtest column takes the rest, so its name has room (#376): in the
// Backtests table the model has a width too, on up to two lines.
export const WIDTH = {
  instruments: 'w-[104px]',
  model: 'w-[120px]',
  return: 'w-[96px]',
  sharpe: 'w-[72px]',
  maxDrawdown: 'w-[120px]',
  when: 'w-[120px]',
  compare: 'w-[44px]',
} as const;
