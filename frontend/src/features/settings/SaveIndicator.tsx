import { Check, CircleAlert, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import { OUTLINE_BUTTON } from '@/components/buttonStyles';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import type { SaveState } from './autosave';

// Saving… shows only when a save takes longer than this; Saved stays this long, then fades out.
const SAVING_AFTER_MS = 300;
const SAVED_FOR_MS = 2000;
const FADE_MS = 300;

type Status = SaveState['status'];
type Shown = 'none' | 'saving' | 'saved' | 'fading' | 'invalid' | 'failed';

const FIRST: Record<Status, Shown> = { idle: 'none', pending: 'none', success: 'saved', error: 'none' };

// What the indicator shows: a field to fix first, then a failure, then the timed Saving… and
// Saved. Each new status starts from its first look, then its timers move it on.
function useShown({ status, invalid = false }: SaveState): Shown {
  const [seen, setSeen] = useState(status);
  const [timed, setTimed] = useState<Shown>(FIRST[status]);
  if (seen !== status) {
    setSeen(status);
    setTimed(FIRST[status]);
  }
  useEffect(() => {
    const later = (shown: Shown, ms: number) => setTimeout(() => setTimed(shown), ms);
    const timers =
      status === 'pending'
        ? [later('saving', SAVING_AFTER_MS)]
        : status === 'success'
          ? [later('fading', SAVED_FOR_MS), later('none', SAVED_FOR_MS + FADE_MS)]
          : [];
    return () => timers.forEach(clearTimeout);
  }, [status]);
  if (invalid) return 'invalid';
  if (status === 'error') return 'failed';
  return timed;
}

const LOOKS = {
  saving: { icon: Loader2, text: 'Saving…', color: 'text-muted-foreground', spin: true },
  saved: { icon: Check, text: 'Saved', color: 'text-primary', spin: false },
  fading: { icon: Check, text: 'Saved', color: 'text-primary opacity-0', spin: false },
  invalid: { icon: CircleAlert, text: 'Not saved', color: 'text-warn', spin: false },
} as const;

// The icon and the words of a state; Couldn't save has no icon.
function Look({ shown }: { shown: Shown }) {
  if (shown === 'none') return null;
  if (shown === 'failed') return <span className="text-down">Couldn't save</span>;
  const { icon: Icon, text, color, spin } = LOOKS[shown];
  return (
    <span className={cn('flex items-center gap-1.5 transition-opacity duration-300', color)}>
      <Icon aria-hidden="true" className={cn('size-3.5', spin && 'animate-spin')} strokeWidth={2.5} />
      {text}
    </span>
  );
}

// At the right of a card's title: Saving…, Saved with a check, Not saved in amber while a field
// holds a value to fix (the field says what), Couldn't save with Retry. Read out as it changes.
// Centred on the title row: the row lines up its parts on their text, and with none the indicator
// would sit lower and the card would move each time it shows or hides.
export function SaveIndicator({ state }: { state: SaveState }) {
  const shown = useShown(state);
  return (
    <div className="flex h-6 items-center gap-2 self-center text-[13px]">
      <output>
        <Look shown={shown} />
      </output>
      {shown === 'failed' && (
        <Button
          type="button"
          variant="outline"
          className={cn('h-6 px-2 text-xs', OUTLINE_BUTTON)}
          onClick={state.retry}
        >
          Retry
        </Button>
      )}
    </div>
  );
}
