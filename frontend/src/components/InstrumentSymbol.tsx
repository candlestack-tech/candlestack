import { InstrumentIcon } from '@/components/InstrumentIcon';
import { cn } from '@/lib/utils';

// The sizes next to a symbol: 16 px in the dashboard's starred rows and a chart's title, 20 px in
// table rows, a backtest's header and lists, 24 px on the builder's instrument button.
type Size = 16 | 20 | 24;

type Props = {
  id: string;
  symbol: string;
  // The instrument's name after the symbol, muted, where there is room for it.
  name?: string;
  size: Size;
  className?: string;
  // The symbol's own classes, e.g. a minimum width that lines up the names of a list.
  symbolClassName?: string;
};

// An instrument wherever the app names one (#318): its icon (InstrumentIcon, lazy like everywhere
// else, so a row asks for nothing but the image), the symbol in mono and optionally the name. It
// keeps to its box: alone, the symbol gives way with an ellipsis; with the name, the name does.
// Either one shows in full in its title.
export function InstrumentSymbol({ id, symbol, name, size, className, symbolClassName }: Props) {
  return (
    <span className={cn('inline-flex max-w-full min-w-0 items-center', size === 24 ? 'gap-2' : 'gap-1.5', className)}>
      <InstrumentIcon id={id} size={size} />
      <span
        title={symbol}
        className={cn('font-mono font-medium text-foreground', name ? 'shrink-0' : 'min-w-0 truncate', symbolClassName)}
      >
        {symbol}
      </span>
      {name && (
        <span title={name} className="min-w-0 truncate font-normal text-muted-foreground">
          {name}
        </span>
      )}
    </span>
  );
}
