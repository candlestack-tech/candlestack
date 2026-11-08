import type { AppClient } from '@/api';
import { LogoMark } from '@/brand/LogoMark';
import { AccentDot } from '@/components/AccentDot';
import { BUTTON_SIZES, GHOST_BUTTON, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { ErrorNote } from '@/components/ErrorNote';
import { Button } from '@/components/ui/button';
import { PROVIDERS, ProviderButton, useProviderSignIn } from '@/features/signin';
import { cn } from '@/lib/utils';

import { AppPair, ConsentFrame, LEAD, StateActions, TITLE } from './parts';

// Signed out (#consent/signed-out): the sign-in page's buttons on the consent card, naming the
// app. Signing in comes back to this page, `next`.
export function SignInToConnect({ client, next }: { client: AppClient; next: string }) {
  const { busy, start } = useProviderSignIn(next);
  return (
    <ConsentFrame center>
      <AppPair client={client} />
      <div>
        <h1 className={TITLE}>Sign in to connect {client.name}</h1>
        <p className={LEAD}>
          {client.name} wants access to your CandleStack account. Sign in first, then choose what it can do.
        </p>
      </div>
      <div className="flex w-full flex-col gap-2.5">
        {PROVIDERS.map((provider) => (
          <ProviderButton key={provider} provider={provider} busy={busy} onStart={start} />
        ))}
      </div>
      <p className="flex items-center justify-center gap-2 text-[12.5px] text-muted-foreground">
        <AccentDot />
        In beta, open to people on the beta list
      </p>
    </ConsentFrame>
  );
}

type DeniedProps = {
  client: AppClient;
  email: string;
  switching: boolean;
  error: Error | null;
  onUseAnotherAccount: () => void;
};

// The account is off the beta list (#consent/denied), in the sign-in page's words: who it is, and
// the two ways on. Use another account signs out and shows the sign-in on this page.
export function NotOnBetaList({ client, email, switching, error, onUseAnotherAccount }: DeniedProps) {
  return (
    <ConsentFrame center>
      <LogoMark className="size-11" />
      <div>
        <h1 className={TITLE}>You are not on the beta list yet</h1>
        <p className={LEAD}>
          <b>{email}</b> cannot sign in during the beta, so {client.name} cannot connect. Ask the team for an invite,
          then try again.
        </p>
      </div>
      <StateActions>
        <Button
          type="button"
          variant="outline"
          className={cn(BUTTON_SIZES.default, OUTLINE_BUTTON)}
          disabled={switching}
          onClick={onUseAnotherAccount}
        >
          Use another account
        </Button>
        <Button asChild variant="ghost" className={cn(BUTTON_SIZES.default, GHOST_BUTTON)}>
          <a href="https://candlestack.tech" target="_blank" rel="noopener">
            Contact the team
          </a>
        </Button>
      </StateActions>
      {error && <ErrorNote>{error.message}</ErrorNote>}
    </ConsentFrame>
  );
}
