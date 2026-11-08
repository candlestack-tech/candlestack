import { Check, Copy, ExternalLink } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';

import type { ApiKey, Scope } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { KeyIcon, WarnIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// The parts of a key the card and its dialogs share (design/mockups/api-mcp): the key's tile,
// its name with the prefix, the scopes as chips, the amber note, the Copy button and the link to
// the API guide.

// .kr-ic: a 34 px tile with the key, on the surface (on the background inside .who-k).
export function KeyTile({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'grid size-[34px] flex-none place-items-center rounded-lg bg-muted text-muted-foreground',
        className,
      )}
    >
      <KeyIcon className="size-[17px]" />
    </span>
  );
}

// .kr-n: the name, cut short when long, then the start of the key in mono ("cs_7Hq2Lm9x…").
export function KeyName({
  apiKey,
  muted = false,
  children,
}: {
  apiKey: ApiKey;
  muted?: boolean;
  children?: ReactNode;
}) {
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <b className={cn('min-w-0 truncate font-medium', muted && 'text-muted-foreground')}>{apiKey.name}</b>
      <code className="font-mono text-[12px] whitespace-nowrap text-muted-foreground">{apiKey.prefix}…</code>
      {children}
    </span>
  );
}

// .chips: the API's names of the scopes, as small mono pills.
export function ScopeChips({ scopes }: { scopes: Scope[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {scopes.map((scope) => (
        <span
          key={scope}
          className="inline-block rounded-full border bg-background px-[7px] font-mono text-[11.5px]/[18px] font-medium whitespace-nowrap"
        >
          {scope}
        </span>
      ))}
    </span>
  );
}

// .note.warn: the warning triangle and the words on an amber wash.
export function WarnNote({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        'flex items-start gap-[9px] rounded-lg border border-[color-mix(in_srgb,var(--warn)_35%,var(--rule))] bg-[color-mix(in_srgb,var(--warn)_11%,var(--background))] px-3 py-2.5 text-[13px]',
        className,
      )}
    >
      <WarnIcon className="mt-0.5 size-[15px] flex-none text-warn" />
      {children}
    </div>
  );
}

type CopyState = 'idle' | 'copied' | 'failed';

// Copies `text`; says Copied, in green, for a few seconds after.
function useCopy(text: string) {
  const [state, setState] = useState<CopyState>('idle');
  useEffect(() => {
    if (state === 'idle') return;
    const timer = setTimeout(() => setState('idle'), 2500);
    return () => clearTimeout(timer);
  }, [state]);
  const copy = () =>
    navigator.clipboard.writeText(text).then(
      () => setState('copied'),
      () => setState('failed'),
    );
  return { state, copy: () => void copy() };
}

const COPIED = 'border-[color-mix(in_srgb,var(--up)_45%,var(--rule))] text-up hover:text-up';
const WORDS: Record<CopyState, string> = { idle: 'Copy', copied: 'Copied', failed: 'Could not copy' };

// .btn.sm with Copy, which turns into a green Copied. Icon only (the example's corner), `label`
// names it for a screen reader until it has copied.
export function CopyButton({ text, label }: { text: string; label?: string }) {
  const { state, copy } = useCopy(text);
  const Icon = state === 'copied' ? Check : Copy;
  const iconOnly = label !== undefined;
  return (
    <Button
      type="button"
      variant="outline"
      aria-label={iconOnly && state === 'idle' ? label : undefined}
      title={iconOnly ? WORDS[state] : undefined}
      className={cn(
        BUTTON_SIZES.sm,
        OUTLINE_BUTTON,
        'flex-none [&_svg]:size-3.5',
        iconOnly && 'w-7 px-0 has-[>svg]:px-0',
        state === 'copied' && COPIED,
      )}
      onClick={copy}
    >
      <Icon aria-hidden="true" />
      {iconOnly ? <span className="sr-only">{state === 'idle' ? '' : WORDS[state]}</span> : WORDS[state]}
    </Button>
  );
}

// The public guide to the API, from the Docs row of the card and the key shown once.
export const API_GUIDE_URL = 'https://candlestack.tech/docs/api';

// .link with the arrow out: the accent, underlined under the pointer.
export function GuideLink({ className }: { className?: string }) {
  return (
    <a
      href={API_GUIDE_URL}
      target="_blank"
      rel="noopener"
      className={cn(
        'inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline hover:underline-offset-2',
        className,
      )}
    >
      API guide
      <ExternalLink aria-hidden="true" className="size-3" />
    </a>
  );
}
