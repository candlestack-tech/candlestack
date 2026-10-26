import type { ReactNode } from 'react';

import { HINT } from '@/components/Hint';
import { cn } from '@/lib/utils';

// What the cards of Settings hold (mockups/app, ddSettings): rows of a label and a control, the
// account's actions, what to fix in a field, and the hints and ids in them.

// A row's top line, except right under the card's title. The rows are the card's own children for
// it, so a form goes around the card, not inside it.
const RULED = 'border-t [[data-slot=card-head]+&]:border-t-0';

type RowProps = { label: string; hint?: ReactNode; top?: boolean; children: ReactNode };

// A 128 px label (13.5 px, its hint 11.5 px under it), 12 px, then the control. `top` lines the
// label up with the top of a tall control instead of its middle.
export function SettingsRow({ label, hint, top = false, children }: RowProps) {
  return (
    <div
      data-slot="settings-row"
      className={cn('grid grid-cols-[128px_minmax(0,1fr)] gap-3 py-[9px]', top ? 'items-start' : 'items-center', RULED)}
    >
      <span className="flex flex-col text-[13.5px]">
        {label}
        {hint && <span className="text-[11.5px] text-muted-foreground">{hint}</span>}
      </span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

// A line under the title saying what the card is for.
export function SettingsLead({ children }: { children: ReactNode }) {
  return <p className={cn('-mt-1 mb-2', HINT)}>{children}</p>;
}

// An action of the account card: what it does over a hint, its button at the right.
export function ActionRow({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className={cn('flex items-center justify-between gap-3 py-2.5', RULED)}>
      <div className="flex flex-col gap-0.5">
        <b>{title}</b>
        <span className={HINT}>{hint}</span>
      </div>
      {children}
    </div>
  );
}

// What to fix in a field whose value cannot be saved, in red under it.
export function FieldError({ children }: { children: ReactNode }) {
  return <p className="mt-1.5 text-[12px]/[1.5] text-down">{children}</p>;
}

// .st-id code: an id in mono on the surface.
export function Code({ children }: { children: ReactNode }) {
  return <code className="rounded-[5px] bg-muted px-2 py-[3px] font-mono text-[13px]">{children}</code>;
}
