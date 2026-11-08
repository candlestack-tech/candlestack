import { Check, Minus } from 'lucide-react';

import { cn } from '@/lib/utils';

// .box of the dialogs (design/mockups/api-mcp): the 18 px box of a checkbox, filled with the accent
// and its check when on, or a dash when only some of what it stands for are on. It draws a visually
// hidden checkbox just before it (`peer`), which keeps the focus and the keyboard.
export function Tick({
  on,
  mixed = false,
  faded = false,
  className,
}: {
  on: boolean;
  mixed?: boolean;
  faded?: boolean;
  className?: string;
}) {
  const filled = on || mixed;
  return (
    <span
      className={cn(
        'mt-px grid size-[18px] place-items-center rounded-[5px] border-[1.5px] text-primary-foreground peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50',
        filled ? 'border-primary bg-primary' : 'border-[color-mix(in_srgb,var(--muted)_60%,var(--rule))]',
        faded && 'opacity-55',
        className,
      )}
    >
      {on && <Check aria-hidden="true" strokeWidth={3} className="size-3" />}
      {!on && mixed && <Minus aria-hidden="true" strokeWidth={3} className="size-3" />}
    </span>
  );
}
