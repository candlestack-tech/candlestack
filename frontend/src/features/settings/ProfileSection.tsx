import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { DashboardCard } from '@/components/DashboardCard';
import { errorMessage, meQueryOptions, updateMe, type Me } from '@/api';
import { ErrorNote } from '@/components/ErrorNote';
import { HINT } from '@/components/Hint';
import { UserIcon } from '@/components/icons';
import { Input } from '@/components/ui/input';
import { Avatar, useSignedInUser } from '@/features/account';
import { NAME_REFUSED } from '@/lib/messages';
import { cn } from '@/lib/utils';

import { textFieldKeys, useAutosave } from './autosave';
import { LeaveGuard } from './LeaveGuard';
import { Code, FieldError, SettingsRow } from './rows';
import { SaveIndicator } from './SaveIndicator';

// The API's limit (MeUpdate); it trims the name, so the check does too.
const NAME_MAX = 80;

// The display name being edited, saved trimmed on leaving the field or on Enter once it changed;
// Esc puts the saved one back. An empty one is not sent: the field says so once left. The API's
// answer becomes the user everywhere (the header, the menu).
function useDisplayName(user: Me) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(user.display_name);
  const [checked, setChecked] = useState(false);
  const autosave = useAutosave({
    key: ['settings', 'profile'],
    initial: user.display_name,
    save: (displayName: string) => updateMe({ display_name: displayName }),
    onSaved: (me) => queryClient.setQueryData(meQueryOptions().queryKey, me),
  });
  const empty = name.trim() === '';
  const saveName = (next: string) => {
    setChecked(true);
    if (next.trim() !== '') autosave.commit(next.trim());
  };
  const revert = () => {
    setName(user.display_name);
    saveName(user.display_name);
  };
  return {
    name,
    setName,
    empty,
    shownEmpty: checked && empty,
    keys: textFieldKeys(() => saveName(name), revert),
    state: { ...autosave.state, invalid: checked && empty },
    error: autosave.error ? errorMessage(autosave.error, NAME_REFUSED) : undefined,
  };
}

// Who is signed in: the drawn initials (44 px) with the name and email.
function Who({ user }: { user: Me }) {
  return (
    <div className="flex items-center gap-3 pt-1 pb-3">
      <Avatar user={user} className="size-11 text-[15px]/[normal]" />
      <span className="flex min-w-0 flex-col">
        <b className="truncate">{user.display_name}</b>
        <span className={cn('truncate', HINT)}>{user.email}</span>
      </span>
    </div>
  );
}

// The user, then the display name to change, the email from the provider and the public user ID.
// The name saves itself; the indicator by the title says how that went.
export function ProfileSection() {
  const user = useSignedInUser();
  const { name, setName, empty, shownEmpty, keys, state, error } = useDisplayName(user);
  return (
    <DashboardCard icon={UserIcon} title="Profile" aside={<SaveIndicator state={state} />}>
      <Who user={user} />
      <SettingsRow label="Display name">
        <Input
          className="max-w-[260px]"
          value={name}
          onChange={(event) => setName(event.target.value)}
          {...keys}
          maxLength={NAME_MAX}
          autoComplete="name"
          aria-label="Display name"
          aria-invalid={shownEmpty || undefined}
        />
        {shownEmpty && <FieldError>Enter a name. Not saved until fixed.</FieldError>}
      </SettingsRow>
      <SettingsRow label="Email">
        <span>{user.email}</span>
      </SettingsRow>
      <SettingsRow label="User ID">
        <Code>{user.id}</Code>
      </SettingsRow>
      {error && <ErrorNote className="mt-2">{error}</ErrorNote>}
      <LeaveGuard when={empty} />
    </DashboardCard>
  );
}
