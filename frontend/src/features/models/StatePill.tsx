import type { ModelState } from '@/api';
import { cn } from '@/lib/utils';

import { STATE_TEXT, STATE_WHY } from './manifest';

const TONES: Record<ModelState, string> = {
  verified: 'text-up border-[color-mix(in_srgb,var(--up)_40%,var(--rule))]',
  ready: 'text-muted-foreground',
  needs_setup: 'text-warn border-[color-mix(in_srgb,var(--warn)_45%,var(--rule))]',
};

// How far a model got (mockups/app, .m3-st): Verified in green, Ready muted, Needs setup in the
// warning colour, each a dot and the word in a pill; its title says what the state means.
export function StatePill({ state }: { state: ModelState }) {
  return (
    <span
      title={STATE_WHY[state]}
      className={cn(
        'inline-flex flex-none items-center gap-[5px] rounded-full border px-[7px] py-px font-mono text-[11.5px]/[18px] font-medium whitespace-nowrap',
        TONES[state],
      )}
    >
      <i aria-hidden="true" className="size-[7px] rounded-full bg-current" />
      {STATE_TEXT[state]}
    </span>
  );
}
