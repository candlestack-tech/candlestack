import { Link } from '@tanstack/react-router';
import { Check, Link2, XIcon } from 'lucide-react';

import { ApiError, type ModelUpload, type StorageUsage } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { ClockIcon, UserIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { useSignedInUser, useSwitchAccount } from '@/features/account';
import { cn } from '@/lib/utils';

import { formatSize } from './format';
import { linkFramework, storedFailure, useLinkUpload, useNow, type Phase } from './useLinkUpload';
import {
  AskedFor,
  CardHint,
  FileRow,
  LinkCard,
  MetaLine,
  StateCard,
  UploadPageFrame,
  UploadProgress,
} from './UploadLinkParts';
import { Done, Failed, LinkChecks, LinkMeta, OpenModel, Ready } from './UploadLinkSteps';
import { PRIMARY, SECONDARY } from './uploadLinkStyles';
import type { UploadFailure } from './useUpload';

// The upload page of a one-time link, /upload/<token> (design/mockups/api-mcp, #upload/*): an
// agent that cannot send a file asks for a link, and the user drops the file here. Signed in only
// (the route's guard); the link answers only the account that asked for it.

const INTRO = 'Your agent asked for this file. It goes from your computer straight to your account.';

const minutesSince = (iso: string, now: number) => Math.max(0, Math.floor((now - Date.parse(iso)) / 60_000));

// "just now", "2 min ago", "3 h ago".
function ago(iso: string, now: number): string {
  const minutes = minutesSince(iso, now);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ago`;
}

const ModelsButton = () => (
  <Button asChild variant="outline" className={SECONDARY}>
    <Link to="/models">Go to Models</Link>
  </Button>
);

function WrongAccount({ token }: { token: string }) {
  const user = useSignedInUser();
  const switchAccount = useSwitchAccount(`/upload/${token}`);
  return (
    <StateCard
      icon={UserIcon}
      tone="warn"
      title="This link is for another account"
      intro={
        <>
          You are signed in as <b>{user.email}</b>. Sign in with the account you use with your agent, then open the link
          again.
        </>
      }
      actions={
        <>
          <Button className={PRIMARY} disabled={switchAccount.isPending} onClick={() => switchAccount.mutate()}>
            Use another account
          </Button>
          <Button asChild variant="outline" className={SECONDARY}>
            <Link to="/dashboard">Go to Dashboard</Link>
          </Button>
        </>
      }
    />
  );
}

// The link could not be read: another account's, unknown, or the request failed.
function LinkError({ error, token, onRetry }: { error: Error; token: string; onRetry: () => void }) {
  if (error instanceof ApiError && error.problem === 'upload-link-of-another-user')
    return <WrongAccount token={token} />;
  if (error instanceof ApiError && error.status === 404) {
    return (
      <StateCard
        icon={XIcon}
        tone="down"
        title="This link does not work"
        intro="Check that the whole link was copied, or ask your agent for a new one."
        actions={<ModelsButton />}
      />
    );
  }
  return (
    <StateCard
      icon={XIcon}
      tone="down"
      title="Could not load this link"
      intro={error.message}
      actions={
        <Button className={PRIMARY} onClick={onRetry}>
          Try again
        </Button>
      }
    />
  );
}

type ViewProps = {
  link: ModelUpload;
  phase: Phase;
  usage: StorageUsage | undefined;
  now: number;
  onFile: (file: File) => void;
  onCancel: () => void;
};

const from = (link: ModelUpload, now: number) => (
  <AskedFor icon={Link2}>Asked for {ago(link.created_at, now)}</AskedFor>
);

type UploadingProps = {
  link: ModelUpload;
  phase: Extract<Phase, { step: 'uploading' }>;
  usage: StorageUsage | undefined;
  now: number;
  onCancel: () => void;
};

function Uploading({ link, phase, usage, now, onCancel }: UploadingProps) {
  const framework = linkFramework(link);
  return (
    <LinkCard top={from(link, now)} title="Upload your model" intro={INTRO}>
      <FileRow name={link.name} framework={framework} line={framework ?? ''}>
        <Button type="button" variant="outline" className={cn(BUTTON_SIZES.sm, OUTLINE_BUTTON)} onClick={onCancel}>
          Cancel
        </Button>
      </FileRow>
      <UploadProgress
        sent={phase.sent}
        total={phase.total}
        sentText={`${formatSize(phase.sent)} of ${formatSize(phase.total)}`}
      />
      <CardHint>Keep this tab open until the upload ends.</CardHint>
      <LinkMeta link={link} usage={usage} now={now} />
    </LinkCard>
  );
}

// This page's upload being checked, or (`elsewhere`) an upload through the link from another tab,
// which the API marks as checking as soon as it starts.
function Checking({ link, now, elsewhere = false }: { link: ModelUpload; now: number; elsewhere?: boolean }) {
  const framework = linkFramework(link);
  return (
    <LinkCard top={from(link, now)} title="Upload your model" intro={INTRO}>
      <FileRow name={link.name} framework={framework} line={`${formatSize(link.size_bytes)} · ${framework ?? ''}`} />
      <LinkChecks state={elsewhere ? 'elsewhere' : 'running'} />
      <MetaLine
        left={
          <>
            <ClockIcon />
            Checks take a few seconds
          </>
        }
      />
    </LinkCard>
  );
}

function FailedCard({ link, failure, now }: { link: ModelUpload; failure: UploadFailure; now: number }) {
  const checkFailed = failure.kind === 'opens' || failure.kind === 'weights';
  return (
    <LinkCard
      top={from(link, now)}
      title={checkFailed ? 'The model did not pass the checks' : 'The upload did not finish'}
      intro={
        checkFailed
          ? 'Nothing was added to your account. Fix the file, then ask your agent for a new link.'
          : 'Nothing was added to your account. Ask your agent for a new link.'
      }
    >
      <Failed link={link} failure={failure} />
    </LinkCard>
  );
}

const Cancelled = () => (
  <StateCard
    icon={XIcon}
    title="Upload cancelled"
    intro="A link takes one upload, so this one is used up. Ask your agent for a new link."
    actions={<ModelsButton />}
  />
);

const Expired = () => (
  <StateCard
    icon={ClockIcon}
    tone="warn"
    title="This link has expired"
    intro="Links work for 15 minutes. Ask your agent for a new one and open it here."
    actions={<ModelsButton />}
  />
);

// The link took its file before this page opened (or in another tab).
function Used({ link, now }: { link: ModelUpload; now: number }) {
  return (
    <StateCard
      icon={Check}
      title="This link was already used"
      intro={
        <>
          Each link takes one file. <b>{link.name}</b> came through it {ago(link.finished_at ?? link.created_at, now)}.
        </>
      }
      actions={
        <>
          {link.model_id && <OpenModel id={link.model_id} />}
          <ModelsButton />
        </>
      }
    />
  );
}

// What the page shows: this page's own upload as it goes, else the link's status.
function LinkView({ link, phase, usage, now, onFile, onCancel }: ViewProps) {
  switch (phase.step) {
    case 'uploading':
      return <Uploading link={link} phase={phase} usage={usage} now={now} onCancel={onCancel} />;
    case 'checking':
      return <Checking link={link} now={now} />;
    case 'done':
      return (
        <LinkCard
          top={from(link, now)}
          title="Model uploaded"
          intro="It is in your Models now. Go back to your agent to add the manifest and run a backtest."
        >
          <Done model={phase.model} usedBefore={phase.usedBefore} usage={usage} />
        </LinkCard>
      );
    case 'failed':
      return <FailedCard link={link} failure={phase.failure} now={now} />;
    case 'cancelled':
      // Cancelled after the whole file had gone out: the API may have made the model anyway.
      return link.status === 'done' ? <Used link={link} now={now} /> : <Cancelled />;
    case 'ready':
      return <ByStatus link={link} phase={phase} usage={usage} now={now} onFile={onFile} />;
  }
}

type StatusProps = Omit<ViewProps, 'onCancel' | 'phase'> & { phase: Extract<Phase, { step: 'ready' }> };

// Before this page uploads anything: the link waits for its file (until its expiry passes on the
// page's clock), expired, is being checked (another tab), took its file, or failed.
function ByStatus({ link, phase, usage, now, onFile }: StatusProps) {
  switch (link.status) {
    case 'waiting':
      if (Date.parse(link.expires_at) <= now) return <Expired />;
      return (
        <LinkCard top={from(link, now)} title="Upload your model" intro={INTRO}>
          <Ready link={link} usage={usage} note={phase.note} now={now} onFile={onFile} />
        </LinkCard>
      );
    case 'expired':
      return <Expired />;
    case 'checking':
      return <Checking link={link} now={now} elsewhere />;
    case 'done':
      return <Used link={link} now={now} />;
    case 'failed':
      return <FailedCard link={link} failure={storedFailure(link, usage)} now={now} />;
  }
}

export function UploadLink({ token }: { token: string }) {
  const { link, usage, phase, choose, cancel } = useLinkUpload(token);
  const now = useNow();
  // A link that is gone or not this account's: React Query keeps the last answer when asking again
  // fails, so the error decides over it.
  const refused = link.error instanceof ApiError && (link.error.status === 403 || link.error.status === 404);
  let view;
  if (link.data && !refused) {
    view = <LinkView link={link.data} phase={phase} usage={usage} now={now} onFile={choose} onCancel={cancel} />;
  } else if (link.error) {
    view = <LinkError error={link.error} token={token} onRetry={() => void link.refetch()} />;
  } else {
    view = <output className="text-sm text-muted-foreground">Loading…</output>;
  }
  return <UploadPageFrame>{view}</UploadPageFrame>;
}
