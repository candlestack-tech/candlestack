import type { ConnectedApp } from '@/api';
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

import { ConnectedAppName, ConnectedAppTile } from './appParts';
import { ScopeChips } from './keyParts';
import { useDisconnectApp } from './useConnectedApps';

// The question before an app goes: the app, what happens, Cancel and the red button.
function DisconnectQuestion({ app, onDone }: { app: ConnectedApp; onDone: () => void }) {
  const disconnect = useDisconnectApp();
  const { name } = app.client;
  return (
    <>
      <DialogHeader>
        <DialogTitle className="min-w-0 truncate">Disconnect {name}?</DialogTitle>
      </DialogHeader>
      <DialogBody>
        <div className="flex min-w-0 items-center gap-2.5 rounded-lg bg-muted px-3 py-2.5">
          <ConnectedAppTile app={app} className="bg-background" />
          <span className="flex min-w-0 flex-1 flex-col gap-1">
            <ConnectedAppName app={app} />
            <ScopeChips scopes={app.scopes} />
          </span>
        </div>
        <DialogDescription className="text-sm text-foreground">
          {name} loses access to your account at once. Its chats keep what they already show.
        </DialogDescription>
        <p className="text-sm text-muted-foreground">You can connect it again from {name} any time.</p>
        {disconnect.error && <ErrorNote>{disconnect.error.message}</ErrorNote>}
      </DialogBody>
      <DialogFooter>
        <CancelButton />
        <DangerButton disabled={disconnect.isPending} onClick={() => disconnect.mutate(app, { onSuccess: onDone })}>
          Disconnect
        </DangerButton>
      </DialogFooter>
    </>
  );
}

type Props = { app: ConnectedApp | null; open: boolean; onOpenChange: (open: boolean) => void };

// Disconnect asks first (design/mockups/api-mcp, #mcp/disconnect). The app stays while the dialog
// fades out; the question is made anew each time, so a failed attempt is forgotten.
export function DisconnectAppDialog({ app, open, onOpenChange }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {app && <DisconnectQuestion key={app.id} app={app} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
