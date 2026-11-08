import { Code as CodeIcon, Plus } from 'lucide-react';
import { useState } from 'react';

import type { ApiKey, ApiKeys } from '@/api';
import { ACCENT_BUTTON, BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { DashboardCard } from '@/components/DashboardCard';
import { ErrorNote } from '@/components/ErrorNote';
import { HINT } from '@/components/Hint';
import { KeyIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { useDateStyle } from '@/features/preferences';
import { formatDate } from '@/lib/dates';
import { useNow } from '@/lib/useNow';
import { cn } from '@/lib/utils';

import { expiryText, isExpired, lastUsedText } from './apiKeyText';
import { CreateKeyDialog } from './CreateKeyDialog';
import { GuideLink, KeyName, KeyTile, ScopeChips, WarnNote } from './keyParts';
import { RevokeKeyDialog } from './RevokeKeyDialog';
import { SettingsLead, SettingsRow } from './rows';
import { useApiKeys } from './useApiKeys';

const TONES = { warn: 'text-warn', down: 'text-down' };

// .tag.err: Expired, in red mono on a red rim.
function ExpiredTag() {
  return (
    <span className="inline-flex rounded border border-[color-mix(in_srgb,var(--down)_45%,var(--rule))] px-1.5 py-px font-mono text-[11px]/4 font-medium whitespace-nowrap text-down">
      Expired
    </span>
  );
}

// .kr-t: when the key was last used, over when it was made and when it stops working.
function KeyTimes({ apiKey, now }: { apiKey: ApiKey; now: number }) {
  const style = useDateStyle();
  const used = lastUsedText(apiKey, style, now);
  const expiry = expiryText(apiKey, style, now);
  return (
    <div className="flex min-w-0 flex-col gap-px text-[12.5px] max-[640px]:col-[2/4] max-[640px]:row-2">
      <span>{used === null ? <span className="text-muted-foreground">Never used</span> : `Last used ${used}`}</span>
      <span className="text-[11.5px] text-muted-foreground">
        Created {formatDate(new Date(apiKey.created_at), style)} ·{' '}
        <span className={cn(expiry.tone && TONES[expiry.tone])}>{expiry.text}</span>
      </span>
    </div>
  );
}

type RowProps = { apiKey: ApiKey; now: number; onRevoke: () => void };

// .kr: the key's tile, its name and prefix over its scopes, its times, and Revoke. An expired key
// is greyed with a red tag, and Delete. On a phone the times go under the name.
function KeyRow({ apiKey, now, onRevoke }: RowProps) {
  const expired = isExpired(apiKey, now);
  const action = expired ? 'Delete' : 'Revoke';
  return (
    <div
      className={cn(
        'grid grid-cols-[34px_minmax(0,1fr)_minmax(0,200px)_auto] items-center gap-3 rounded-lg border px-3 py-2.5',
        'max-[640px]:grid-cols-[34px_minmax(0,1fr)_auto] max-[640px]:gap-y-1.5',
        expired && 'bg-muted',
      )}
    >
      <KeyTile />
      <div className="flex min-w-0 flex-col gap-1">
        <KeyName apiKey={apiKey} muted={expired}>
          {expired && <ExpiredTag />}
        </KeyName>
        <ScopeChips scopes={apiKey.scopes} />
      </div>
      <KeyTimes apiKey={apiKey} now={now} />
      <Button
        type="button"
        variant="outline"
        aria-label={`${action} ${apiKey.name}`}
        className={cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'max-[640px]:col-3 max-[640px]:row-1')}
        onClick={onRevoke}
      >
        {action}
      </Button>
    </div>
  );
}

type CreateProps = { className?: string; disabled?: boolean; title?: string; onClick: () => void };

function CreateButton({ className, disabled, title, onClick }: CreateProps) {
  return (
    <Button
      type="button"
      className={cn(BUTTON_SIZES.sm, ACCENT_BUTTON, className)}
      disabled={disabled}
      title={title}
      onClick={onClick}
    >
      <Plus aria-hidden="true" />
      Create key
    </Button>
  );
}

// .st-empty: the key on a tile, what to do, Create key.
function NoKeys({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 pt-[26px] pb-[22px] text-center">
      <span className="mb-1 grid size-[38px] place-items-center rounded-[10px] bg-accent-soft text-primary">
        <KeyIcon className="size-[19px]" />
      </span>
      <b className="text-[14.5px] font-semibold">No API keys yet</b>
      <span className={HINT}>Create one for a script, Claude Code or Cursor.</span>
      <CreateButton className="mt-2" onClick={onCreate} />
    </div>
  );
}

// How many keys work (expired ones do not count) and whether that is the limit.
const usage = ({ items, limit }: ApiKeys, now: number) => {
  const working = items.filter((key) => !isExpired(key, now)).length;
  return { working, limit, full: working >= limit };
};

// The count of the limit next to Create key, at the right of the title. At the limit both turn off.
function KeysAside({ keys, now, onCreate }: { keys: ApiKeys; now: number; onCreate: () => void }) {
  const { working, limit, full } = usage(keys, now);
  return (
    <div className="flex items-center gap-2.5">
      <span className={cn('font-mono text-[12px] text-muted-foreground', full && 'text-warn')}>
        {working} of {limit}
      </span>
      <CreateButton
        disabled={full}
        title={full ? `${limit} keys is the most. Revoke one first.` : undefined}
        onClick={onCreate}
      />
    </div>
  );
}

type ListProps = { keys: ApiKeys; now: number; onRevoke: (key: ApiKey) => void };

// The keys, oldest first; at the limit an amber note above them says to revoke one.
function KeyList({ keys, now, onRevoke }: ListProps) {
  const { limit, full } = usage(keys, now);
  return (
    <>
      {full && (
        <WarnNote className="mb-2">
          <span>You have {limit} keys, the most there can be. Revoke one to create another.</span>
        </WarnNote>
      )}
      <div className="flex flex-col gap-2">
        {keys.items.map((key) => (
          <KeyRow key={key.id} apiKey={key} now={now} onRevoke={() => onRevoke(key)} />
        ))}
      </div>
    </>
  );
}

// What went wrong loading the keys, and Try again.
function LoadError({ error, retrying, onRetry }: { error: Error; retrying: boolean; onRetry: () => void }) {
  return (
    <div className="mb-2 flex items-center gap-3">
      <ErrorNote>{error.message}</ErrorNote>
      <Button
        type="button"
        variant="outline"
        className={cn(BUTTON_SIZES.sm, OUTLINE_BUTTON)}
        disabled={retrying}
        onClick={onRetry}
      >
        Try again
      </Button>
    </div>
  );
}

// The API keys card (design/mockups/api-mcp, keysCard): the keys with how many of the limit work,
// Create key, and the API docs. The time moves every minute, so a key that expires while Settings
// is open stops counting and its last use stays right.
export function ApiKeysCard() {
  const { data: keys, error, isFetching, refetch } = useApiKeys();
  const now = useNow(true, 60_000);
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<{ key: ApiKey | null; open: boolean }>({ key: null, open: false });
  const create = () => setCreating(true);
  const any = keys !== undefined && keys.items.length > 0;
  return (
    <DashboardCard
      icon={CodeIcon}
      title="API keys"
      aside={any && <KeysAside keys={keys} now={now} onCreate={create} />}
    >
      <SettingsLead>Call the API from your scripts and agents. A key acts as you, so keep it secret.</SettingsLead>
      {error && <LoadError error={error} retrying={isFetching} onRetry={() => void refetch()} />}
      {any && <KeyList keys={keys} now={now} onRevoke={(key) => setRevoking({ key, open: true })} />}
      {keys?.items.length === 0 && <NoKeys onCreate={create} />}
      <SettingsRow label="Docs" hint="Every endpoint, with examples">
        <GuideLink />
      </SettingsRow>
      <CreateKeyDialog open={creating} onOpenChange={setCreating} />
      <RevokeKeyDialog
        apiKey={revoking.key}
        open={revoking.open}
        onOpenChange={(open) => setRevoking((state) => ({ ...state, open }))}
      />
    </DashboardCard>
  );
}
