import type { ApiKey } from '@/api';
import { CancelButton, DangerButton } from '@/components/DialogButtons';
import { ErrorNote } from '@/components/ErrorNote';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useDateStyle } from '@/features/preferences';
import { formatDate } from '@/lib/dates';
import { useNow } from '@/lib/useNow';

import { isExpired, lastUsedSentence } from './apiKeyText';
import { KeyName, KeyTile, ScopeChips } from './keyParts';
import { useRevokeApiKey } from './useApiKeys';

const SENTENCE = 'text-sm text-foreground';

// What going means: a working key stops at once, an expired one only leaves the list.
function Consequence({ apiKey, expired, now }: { apiKey: ApiKey; expired: boolean; now: number }) {
  const style = useDateStyle();
  if (expired && apiKey.expires_at !== null) {
    const day = formatDate(new Date(apiKey.expires_at), style);
    return (
      <DialogDescription className={SENTENCE}>
        It expired on {day} and no longer works. This removes it from the list.
      </DialogDescription>
    );
  }
  return (
    <>
      <DialogDescription className={SENTENCE}>
        Anything that uses this key stops working at once. {lastUsedSentence(apiKey, style, now)}
      </DialogDescription>
      <p className="text-sm text-muted-foreground">This cannot be undone. You can create a new key any time.</p>
    </>
  );
}

// The question before a key goes: the key, what happens, Cancel and the red button. A working key
// is revoked; an expired one, which does not work, is deleted from the list.
function RevokeQuestion({ apiKey, onDone }: { apiKey: ApiKey; onDone: () => void }) {
  const revoke = useRevokeApiKey();
  const now = useNow(false);
  const expired = isExpired(apiKey, now);
  return (
    <>
      <DialogHeader>
        <DialogTitle className="min-w-0 truncate">
          {expired ? 'Delete' : 'Revoke'} {apiKey.name}?
        </DialogTitle>
      </DialogHeader>
      <DialogBody>
        <div className="flex min-w-0 items-center gap-2.5 rounded-lg bg-muted px-3 py-2.5">
          <KeyTile className="bg-background" />
          <span className="flex min-w-0 flex-1 flex-col gap-1">
            <KeyName apiKey={apiKey} />
            <ScopeChips scopes={apiKey.scopes} />
          </span>
        </div>
        <Consequence apiKey={apiKey} expired={expired} now={now} />
        {revoke.error && <ErrorNote>{revoke.error.message}</ErrorNote>}
      </DialogBody>
      <DialogFooter>
        <CancelButton />
        <DangerButton disabled={revoke.isPending} onClick={() => revoke.mutate(apiKey, { onSuccess: onDone })}>
          {expired ? 'Delete key' : 'Revoke key'}
        </DangerButton>
      </DialogFooter>
    </>
  );
}

type Props = { apiKey: ApiKey | null; open: boolean; onOpenChange: (open: boolean) => void };

// Revoke and Delete ask first (design/mockups/api-mcp, MODALS.revoke). The key stays while the
// dialog fades out; the question is made anew each time, so a failed attempt is forgotten.
export function RevokeKeyDialog({ apiKey, open, onOpenChange }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {apiKey && <RevokeQuestion key={apiKey.id} apiKey={apiKey} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
