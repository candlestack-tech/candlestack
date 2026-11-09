import * as React from 'react';
import * as SwitchPrimitive from '@radix-ui/react-switch';

import { cn } from '@/lib/utils';

// shadcn/ui's Switch with the app design's (mockups/app, .st-sw): a 38 × 22 pill, grey off (3:1
// on the page and under the knob) and the accent on, its 16 px knob sliding 16 px over. It takes
// a tap 3 px around the pill too, a 44 × 28 target (WCAG 2.5.8 asks for 24 × 24).
function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'relative h-[22px] w-[38px] shrink-0 cursor-pointer rounded-full bg-control-off transition-colors duration-180 outline-none after:absolute after:-inset-[3px] focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-default disabled:opacity-45 data-[state=checked]:bg-primary',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none absolute top-[3px] left-[3px] block size-4 rounded-full bg-background shadow-[0_1px_3px_var(--shadow)] transition-transform duration-180 data-[state=checked]:translate-x-4"
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
