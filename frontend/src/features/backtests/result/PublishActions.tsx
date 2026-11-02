import { Globe, Link as LinkIcon } from 'lucide-react';
import { useId, useState } from 'react';

import type { BacktestDocument } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { useToast } from '@/components/Toast';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { PublishDialog, UnpublishDialog } from './PublishDialogs';

const SMALL = cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'font-medium');

// Why a backtest that has not finished cannot be published.
const NOT_YET: Record<'running' | 'failed', string> = {
  failed: 'A failed backtest has no results to publish. Only finished ones can go to Experiments.',
  running: 'You can publish it once it finishes.',
};

// The green pill after the status of a published backtest (public-experiments, .pub-pill).
export function PublicPill() {
  return (
    <span
      title="Everyone on the CandleStack beta can see it"
      className="inline-flex h-5 items-center gap-1 rounded-full border border-[color-mix(in_srgb,var(--up)_40%,var(--rule))] bg-[color-mix(in_srgb,var(--up)_12%,var(--bg))] pr-2 pl-1.5 font-mono text-[11px]/none font-medium whitespace-nowrap text-up"
    >
      <Globe aria-hidden="true" strokeWidth={2.2} className="size-[11px]" />
      Public
    </span>
  );
}

// Publish, faded, with why it is off under it on hover or focus (.tipw, .tip). It stays in the
// tab order (aria-disabled, not disabled), so the keyboard reaches the reason too.
function PublishOff({ reason }: { reason: string }) {
  const id = useId();
  return (
    <span className="group/tip relative inline-flex">
      <Button
        type="button"
        variant="outline"
        aria-disabled="true"
        aria-describedby={id}
        className={cn(SMALL, 'cursor-default opacity-45 hover:bg-background dark:hover:bg-background')}
      >
        <Globe aria-hidden="true" />
        Publish
      </Button>
      <span
        role="tooltip"
        id={id}
        className="pointer-events-none absolute top-[calc(100%+8px)] right-0 z-40 w-max max-w-[260px] -translate-y-0.5 rounded-md bg-foreground px-2.5 py-[7px] text-[12.5px]/[1.4] whitespace-normal text-background opacity-0 shadow-[0_8px_24px_var(--shadow)] transition-[opacity,translate] duration-100 group-focus-within/tip:translate-y-0 group-focus-within/tip:opacity-100 group-hover/tip:translate-y-0 group-hover/tip:opacity-100 before:absolute before:-top-1 before:right-[22px] before:size-2 before:rotate-45 before:bg-foreground motion-reduce:transition-none"
      >
        {reason}
      </span>
    </span>
  );
}

// Copies the backtest's address; it opens for the accounts of the beta only.
async function copyLink(id: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(`${window.location.origin}/backtests/${id}`);
    return true;
  } catch {
    return false;
  }
}

type Props = { doc: BacktestDocument };

// Copy link and Unpublish, in the place of Publish once published.
function PublishedButtons({
  id,
  onCopied,
  onUnpublish,
}: {
  id: string;
  onCopied: (text: string) => void;
  onUnpublish: () => void;
}) {
  return (
    <>
      <Button
        type="button"
        variant="outline"
        className={SMALL}
        onClick={() =>
          void copyLink(id).then((copied) => {
            onCopied(
              copied ? 'Link copied. It opens only for people on the CandleStack beta.' : 'Could not copy the link.',
            );
          })
        }
      >
        <LinkIcon aria-hidden="true" />
        Copy link
      </Button>
      <Button type="button" variant="outline" className={SMALL} onClick={onUnpublish}>
        Unpublish
      </Button>
    </>
  );
}

// The user's own backtest, between Download JSON and Add to compare (public-experiments, pageBt):
// Publish (an outline button with a globe) opening its dialog; once published, Copy link and
// Unpublish in its place. Running or failed, Publish is off and says why. Each change ends in a
// toast.
export function PublishActions({ doc }: Props) {
  const [dialog, setDialog] = useState<'publish' | 'unpublish' | null>(null);
  const { toast, show } = useToast();
  const { backtest } = doc;
  const onOpenChange = (open: boolean) => {
    if (!open) setDialog(null);
  };
  const done = (text: string) => () => {
    setDialog(null);
    show(text);
  };
  if (backtest.status !== 'finished') return <PublishOff reason={NOT_YET[backtest.status]} />;
  return (
    <>
      {backtest.public ? (
        <PublishedButtons id={backtest.id} onCopied={show} onUnpublish={() => setDialog('unpublish')} />
      ) : (
        <Button type="button" variant="outline" className={SMALL} onClick={() => setDialog('publish')}>
          <Globe aria-hidden="true" />
          Publish
        </Button>
      )}
      <PublishDialog
        doc={doc}
        open={dialog === 'publish'}
        onOpenChange={onOpenChange}
        onDone={done(`Backtest #${backtest.number} is in Experiments now`)}
      />
      <UnpublishDialog
        doc={doc}
        open={dialog === 'unpublish'}
        onOpenChange={onOpenChange}
        onDone={done(`Backtest #${backtest.number} left Experiments`)}
      />
      {toast}
    </>
  );
}
