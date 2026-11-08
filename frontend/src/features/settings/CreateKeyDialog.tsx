import { useIsMutating } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { errorMessage, type ApiKeyCreated, type Scope } from '@/api';
import { CancelButton, PrimaryButton } from '@/components/DialogButtons';
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
import { NAME_REFUSED } from '@/lib/messages';
import { useNow } from '@/lib/useNow';

import { EXPIRIES, SCOPES, expiryHint, type Expiry } from './apiKeyText';
import { ExpiryField, NameField, ScopeBoxes } from './keyFields';
import { ShownKey } from './ShownKey';
import { CREATE_API_KEY, useCreateApiKey } from './useApiKeys';

type Created = { key: ApiKeyCreated; expiry: Expiry };

// The scopes a new key starts with: read and run backtests.
const FIRST_SCOPES: Scope[] = ['read', 'backtests'];

// The hint that the key shows once, Cancel and Create key.
function CreateFooter({ disabled }: { disabled: boolean }) {
  return (
    <DialogFooter>
      <DialogDescription className="mr-auto text-[12px] max-[640px]:hidden">
        You see the key once, right after this
      </DialogDescription>
      <CancelButton />
      <PrimaryButton type="submit" disabled={disabled}>
        Create key
      </PrimaryButton>
    </DialogFooter>
  );
}

// Name, scopes and expiry (MODALS.create). Create key is off while the name is blank; the key
// comes back with its secret, for the next step.
function CreateForm({ onCreated }: { onCreated: (created: Created) => void }) {
  const [name, setName] = useState('');
  const [chosen, setChosen] = useState(() => new Set(FIRST_SCOPES));
  const [expiry, setExpiry] = useState<Expiry>('90');
  const create = useCreateApiKey();
  const style = useDateStyle();
  const now = useNow(false);
  const trimmed = name.trim();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const days = EXPIRIES.find((choice) => choice.value === expiry)?.days ?? null;
    const scopes = SCOPES.map(({ scope }) => scope).filter((scope) => chosen.has(scope));
    create.mutate({ name: trimmed, scopes, expires_in_days: days }, { onSuccess: (key) => onCreated({ key, expiry }) });
  };
  const toggle = (scope: Scope) => {
    const next = new Set(chosen);
    if (!next.delete(scope)) next.add(scope);
    setChosen(next);
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="gap-4">
        <NameField
          value={name}
          onChange={(value) => {
            setName(value);
            create.reset();
          }}
        />
        <ScopeBoxes chosen={chosen} onToggle={toggle} />
        <ExpiryField value={expiry} hint={expiryHint(expiry, style, now)} onChange={setExpiry} />
        {create.error && <ErrorNote>{errorMessage(create.error, NAME_REFUSED)}</ErrorNote>}
      </DialogBody>
      <CreateFooter disabled={trimmed === '' || create.isPending} />
    </form>
  );
}

// The dialog's two steps, inside its content, which unmounts once the dialog has closed: the
// secret is forgotten then, and the dialog opens on the form again.
function CreateSteps({ onShown }: { onShown: () => void }) {
  const [created, setCreated] = useState<Created | null>(null);
  const show = (next: Created) => {
    setCreated(next);
    onShown();
  };
  return (
    <>
      <DialogHeader>
        <DialogTitle>{created ? 'Copy your new key' : 'Create API key'}</DialogTitle>
      </DialogHeader>
      {created ? <ShownKey apiKey={created.key} expiry={created.expiry} /> : <CreateForm onCreated={show} />}
    </>
  );
}

type Props = { open: boolean; onOpenChange: (open: boolean) => void };

// Create API key, then Copy your new key in the same dialog (design/mockups/api-mcp). It stays
// open while the key is on its way, so the secret is never lost to an early close. Once the key
// shows, a click beside the dialog does not close it: Done, the cross or Escape do.
export function CreateKeyDialog({ open, onOpenChange }: Props) {
  const pending = useIsMutating({ mutationKey: CREATE_API_KEY }) > 0;
  const [shown, setShown] = useState(false);
  const change = (next: boolean) => {
    if (pending) return;
    if (!next) setShown(false);
    onOpenChange(next);
  };
  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogContent
        className="w-[min(600px,calc(100%-32px))]"
        onInteractOutside={(event) => {
          if (shown) event.preventDefault();
        }}
      >
        <CreateSteps onShown={() => setShown(true)} />
      </DialogContent>
    </Dialog>
  );
}
