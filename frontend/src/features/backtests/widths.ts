// The widths of the columns the tables of backtests share (#315), padding included: room for the
// longest value and for the header with its sort arrow in the app's fonts, so neither is ever cut.
// "When" fits "2025-10-13 4:05 PM", the instruments one symbol of seven letters with its icon (a
// "+N" after it cuts the symbol). In the Backtests table (table-fixed) the model takes the rest.
export const WIDTH = {
  backtest: 'w-[84px]',
  instruments: 'w-[112px]',
  status: 'w-[88px]',
  return: 'w-[84px]',
  sharpe: 'w-[76px]',
  maxDrawdown: 'w-[128px]',
  when: 'w-[144px]',
} as const;
