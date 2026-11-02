import { Check, Globe, Users, X } from 'lucide-react';
import type { ReactNode } from 'react';

import { errorMessage, type BacktestDocument } from '@/api';
import { CancelButton, PrimaryButton } from '@/components/DialogButtons';
import { Hint } from '@/components/Hint';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { formatCount } from '@/lib/results';
import { cn } from '@/lib/utils';

import { AuthorAvatar } from '../experiments/AuthorAvatar';
import { ExperimentCard } from '../experiments/ExperimentCard';
import { cardOfDocument } from '../experiments/format';
import { usePublishBacktest } from '../useBacktests';

// The dialogs of Publish and Unpublish (public-experiments, MODALS.publish and .unpublish).

type Results = NonNullable<BacktestDocument['results']>;
type DialogProps = { doc: BacktestDocument; open: boolean; onOpenChange: (open: boolean) => void; onDone: () => void };

// One line of what others see (a green check) or do not (a muted cross), what it means under it.
function Item({ shown, title, children }: { shown: boolean; title: string; children?: ReactNode }) {
  return (
    <li className="flex items-start gap-[9px] text-[13px]/[1.4]">
      <span
        aria-hidden="true"
        className={cn(
          'grid size-[18px] flex-none place-items-center rounded-full',
          shown ? 'bg-[color-mix(in_srgb,var(--up)_15%,var(--bg))] text-up' : 'bg-surface-2 text-muted-foreground',
        )}
      >
        {shown ? <Check strokeWidth={2.5} className="size-[11px]" /> : <X strokeWidth={2.5} className="size-[11px]" />}
      </span>
      <span>
        {title}
        {children && (
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">{children}</span>
        )}
      </span>
    </li>
  );
}

function List({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-2.5 text-[13px] font-semibold">{title}</h3>
      <ul className="flex flex-col gap-2.5">{children}</ul>
    </div>
  );
}

// Others see the results, the setup, the model by name and what it reads, and the author; never
// the model file, the email or anything else of the account.
function WhatShows({ doc, results }: { doc: BacktestDocument; results: Results }) {
  const card = cardOfDocument(doc, results);
  const reads = [card.representation, card.timeframe, card.input].filter(Boolean).join(' · ');
  const { name } = doc.backtest.author;
  const trades = results.trades.total;
  return (
    <div className="flex flex-col gap-[18px] pt-1">
      <List title="Others see">
        <Item
          shown
          title={
            trades === 1 ? 'Results, charts and the 1 trade' : `Results, charts and all ${formatCount(trades)} trades`
          }
        />
        <Item shown title="The setup">
          Data, decision, fees, benchmark
        </Item>
        <Item shown title="The model’s name and what it reads">
          <span className="font-mono text-[11.5px]">
            {doc.model.name} · {reads}
          </span>
        </Item>
        <Item shown title="Your name and initials">
          <AuthorAvatar name={name} size="xxs" />
          {name}
        </Item>
      </List>
      <List title="Others don’t see">
        <Item shown={false} title="The model file">
          No download, no copy, no Run again
        </Item>
        <Item shown={false} title="Your email" />
        <Item shown={false} title="Your other backtests and models" />
      </List>
    </div>
  );
}

// The card it gets in Experiments (296 px) beside what others see; one over the other under 980 px.
function Preview({ doc, results }: { doc: BacktestDocument; results: Results }) {
  return (
    <div className="grid grid-cols-[296px_minmax(0,1fr)] items-start gap-[22px] max-[980px]:grid-cols-[minmax(0,1fr)]">
      <div className="flex flex-col gap-2 rounded-[10px] bg-muted p-3">
        <span className="font-mono text-[11px]/4 font-medium tracking-[0.06em] text-muted-foreground uppercase">
          Your card in Experiments
        </span>
        <ExperimentCard facts={cardOfDocument(doc, results)} />
      </div>
      <WhatShows doc={doc} results={results} />
    </div>
  );
}

function Failure({ error, fallback }: { error: Error | null; fallback: string }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-[13px] text-down">
      {errorMessage(error, fallback)}
    </p>
  );
}

// Publish backtest #13? (800 px): who can see it, the card it gets in Experiments beside what
// others see and do not, then Publish. The preview and the lists stack under 980 px.
export function PublishDialog({ doc, open, onOpenChange, onDone }: DialogProps) {
  const publish = usePublishBacktest();
  const { results, backtest } = doc;
  const send = () => {
    publish.mutate({ id: backtest.id, published: true }, { onSuccess: onDone });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        publish.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="w-[min(800px,calc(100%-32px))]">
        <DialogHeader>
          <DialogTitle>Publish backtest #{backtest.number}?</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="flex items-center gap-2.5 rounded-lg bg-accent-soft px-3 py-2.5 text-[13px]">
            <Users aria-hidden="true" className="size-4 flex-none text-primary" />
            Everyone on the CandleStack beta can see it. It never goes on the public web.
          </p>
          {results && <Preview doc={doc} results={results} />}
          <Failure error={publish.error} fallback="Could not publish the backtest. Try again." />
        </DialogBody>
        <DialogFooter>
          <Hint className="mr-auto max-[640px]:hidden">You can unpublish it any time</Hint>
          <CancelButton />
          <PrimaryButton disabled={publish.isPending} onClick={send}>
            <Globe aria-hidden="true" />
            Publish
          </PrimaryButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Unpublish backtest #13? (560 px): it also leaves the Compare of others, whose panel says so.
export function UnpublishDialog({ doc, open, onOpenChange, onDone }: DialogProps) {
  const publish = usePublishBacktest();
  const { backtest } = doc;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        publish.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Unpublish backtest #{backtest.number}?</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p>
            It leaves Experiments now. It also leaves the Compare of anyone who added it, and their panel says it was
            unpublished.
          </p>
          <p className="text-muted-foreground">The backtest stays in your Backtests. You can publish it again later.</p>
          <Failure error={publish.error} fallback="Could not unpublish the backtest. Try again." />
        </DialogBody>
        <DialogFooter>
          <CancelButton />
          <PrimaryButton
            disabled={publish.isPending}
            onClick={() => {
              publish.mutate({ id: backtest.id, published: false }, { onSuccess: onDone });
            }}
          >
            Unpublish
          </PrimaryButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
