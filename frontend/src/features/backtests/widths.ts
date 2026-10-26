// The widths of the columns the tables of backtests share (#315), padding included: room for the
// longest value and for the header with its sort arrow in the app's fonts, so neither is ever cut.
// Return fits "+9,999.9 %", When "28 May 2025 11:58 AM" (the longest of every date and time
// format), the instruments one symbol of seven letters with its icon (a "+N" after it cuts the
// symbol). In the Backtests table (table-fixed) the model takes the rest.
export const WIDTH = {
  backtest: 'w-[84px]',
  instruments: 'w-[112px]',
  status: 'w-[88px]',
  return: 'w-[100px]',
  sharpe: 'w-[76px]',
  maxDrawdown: 'w-[128px]',
  when: 'w-[160px]',
} as const;
