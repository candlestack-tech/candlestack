import { Check } from 'lucide-react';
import { useEffect, useRef, type ReactNode } from 'react';

import type { Scope } from '@/api';
import { HINT } from '@/components/Hint';
import { Input } from '@/components/ui/input';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';

import { EXPIRIES, NAME_MAX, SCOPES, type Expiry } from './apiKeyText';

// The fields of the key dialogs (design/mockups/api-mcp, MODALS.create and MODALS.shown).

// .fld-l: the field's label, with what sits at its right in small mono.
const LABEL = 'flex items-baseline justify-between gap-3 text-[13px] font-medium';
const SIDE = 'font-mono text-[11.5px] font-normal text-muted-foreground';

// .seg: the dialogs' segmented choices, 32 px with 12 px either side and room for a logo.
export const SEGMENT = 'inline-flex h-8 items-center gap-1.5 px-3';

// .fld: the label line, then the control and its hint.
export function Field({ label, side, children }: { label: ReactNode; side?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className={LABEL}>
        {label}
        {side && <span className={SIDE}>{side}</span>}
      </div>
      {children}
    </div>
  );
}

// The key's name with its count of characters; focused as the dialog opens, to type at once.
export function NameField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="api-key-name" className={LABEL}>
        Name
        <span className={SIDE}>
          {value.length} / {NAME_MAX}
        </span>
      </label>
      <Input
        ref={input}
        id="api-key-name"
        value={value}
        maxLength={NAME_MAX}
        placeholder="Claude Code on my laptop"
        autoComplete="off"
        className="h-9"
        onChange={(event) => onChange(event.target.value)}
      />
      <span className={HINT}>So you know where it is used</span>
    </div>
  );
}

type ScopeProps = { scope: Scope; label: string; description: string; on: boolean; onToggle: () => void };

// .sc: a box, the scope in plain words with its API name, what it lets the key do. Read is always
// on (every other scope needs it), its box faded and "Always" at the right.
function ScopeBox({ scope, label, description, on, onToggle }: ScopeProps) {
  const fixed = scope === 'read';
  return (
    <label
      className={cn(
        'grid grid-cols-[18px_minmax(0,1fr)_auto] items-start gap-2.5 bg-background px-3 py-2.5 transition-colors [&+&]:border-t',
        fixed ? 'cursor-default' : 'cursor-pointer hover:bg-muted',
      )}
    >
      <input
        type="checkbox"
        checked={on}
        disabled={fixed}
        aria-label={label}
        aria-describedby={`scope-${scope}`}
        onChange={onToggle}
        className="peer sr-only"
      />
      <span
        className={cn(
          'mt-px grid size-[18px] place-items-center rounded-[5px] border-[1.5px] text-primary-foreground peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50',
          on ? 'border-primary bg-primary' : 'border-[color-mix(in_srgb,var(--muted)_60%,var(--rule))]',
          fixed && 'opacity-55',
        )}
      >
        {on && <Check aria-hidden="true" strokeWidth={3} className="size-3" />}
      </span>
      <span className="flex min-w-0 flex-col gap-px">
        <b className="inline-flex items-baseline gap-2 text-[13.5px] font-medium">
          {label}
          <code className="font-mono text-[11.5px] font-normal text-muted-foreground">{scope}</code>
        </b>
        <span id={`scope-${scope}`} className="text-[12.5px] text-muted-foreground">
          {description}
        </span>
      </span>
      <span className="pt-px text-[11.5px] text-muted-foreground">{fixed ? 'Always' : ''}</span>
    </label>
  );
}

// .scs: the four scopes in one bordered box.
export function ScopeBoxes({ chosen, onToggle }: { chosen: Set<Scope>; onToggle: (scope: Scope) => void }) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className={cn(LABEL, 'mb-1.5 p-0')}>What it can do</legend>
      <div className="flex flex-col overflow-hidden rounded-lg border">
        {SCOPES.map((item) => (
          <ScopeBox key={item.scope} {...item} on={chosen.has(item.scope)} onToggle={() => onToggle(item.scope)} />
        ))}
      </div>
    </fieldset>
  );
}

// 30 days, 90 days, 1 year or Never, and the day it would stop working.
export function ExpiryField({
  value,
  hint,
  onChange,
}: {
  value: Expiry;
  hint: string;
  onChange: (value: Expiry) => void;
}) {
  return (
    <Field label="Expires">
      <ToggleGroup aria-label="Expires" value={value} onValueChange={onChange}>
        {EXPIRIES.map((choice) => (
          <ToggleGroupItem key={choice.value} value={choice.value} className={SEGMENT}>
            {choice.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <span className={HINT}>{hint}</span>
    </Field>
  );
}
