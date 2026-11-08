import { Check, Minus, XIcon } from 'lucide-react';
import type { ReactNode } from 'react';

import { HINT } from '@/components/Hint';
import { cn } from '@/lib/utils';

// `passed`; `failed`, with why in its hint; `running`, being checked now; `todo`, not done yet;
// `none`, nothing to check (no example).
export type CheckState = 'passed' | 'failed' | 'running' | 'todo' | 'none';
export type CheckItem = { state: CheckState; title: string; hint?: string | undefined };

const SPOKEN: Record<CheckState, string> = {
  passed: 'Passed',
  failed: 'Failed',
  running: 'Checking',
  todo: 'Not yet',
  none: 'Not checked',
};

const MARK: Record<CheckState, string> = {
  passed: 'bg-[color-mix(in_srgb,var(--up)_15%,var(--bg))] text-up',
  failed: 'bg-[color-mix(in_srgb,var(--down)_15%,var(--bg))] text-down',
  running:
    'border-2 border-primary border-r-transparent animate-spin motion-reduce:animate-none motion-reduce:border-r-primary',
  todo: 'bg-surface-2 text-muted-foreground',
  none: 'bg-surface-2 text-muted-foreground',
};

const ICON: Record<CheckState, ReactNode> = {
  passed: <Check aria-hidden="true" />,
  failed: <XIcon aria-hidden="true" />,
  running: null,
  todo: <Minus aria-hidden="true" />,
  none: <Minus aria-hidden="true" />,
};

// A check (.ck-r): passed on a green circle, failed on a red one with why under it, running as a
// spinning accent ring, not done yet (or nothing to check) on a muted one with the title muted.
// The model's page and the upload page of a link.
export function CheckRow({ state, title, hint }: CheckItem) {
  const muted = state === 'todo' || state === 'none';
  return (
    <li className="flex items-start gap-2.5">
      <span
        aria-hidden="true"
        className={cn('grid size-[22px] flex-none place-items-center rounded-full [&_svg]:size-[13px]', MARK[state])}
      >
        {ICON[state]}
      </span>
      <span className="min-w-0">
        <span className="sr-only">{SPOKEN[state]}: </span>
        <b className={cn('font-medium', muted && 'text-muted-foreground', state === 'failed' && 'text-down')}>
          {title}
        </b>
        {hint && <span className={cn('block break-words', HINT)}>{hint}</span>}
      </span>
    </li>
  );
}
