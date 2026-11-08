import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Info, Lock, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';

import {
  ApiError,
  decideAuthorizationRequest,
  meQueryOptions,
  type AppClient,
  type AuthorizationRequest,
  type Me,
  type Scope,
} from '@/api';
import { ACCENT_BUTTON, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { ErrorNote } from '@/components/ErrorNote';
import { HINT } from '@/components/Hint';
import { Button } from '@/components/ui/button';
import { Avatar, useSignOutHere } from '@/features/account';
import { SCOPES, ScopeBox } from '@/features/settings';
import { cn } from '@/lib/utils';

import { AppPair, ConsentFrame, ExpiredRequest, LEAD, TITLE } from './parts';
import { NotOnBetaList } from './SignInToConnect';

// What the boxes start with (design/mockups/api-mcp, Notes): read always, Run backtests and
// Manage models on, Publish off.
const STARTS_ON: ReadonlySet<Scope> = new Set(['read', 'backtests', 'models']);

type ScopeRow = (typeof SCOPES)[number];

// The scopes to show: read always (the API grants it whatever the app asks), then those the app
// asked for, in the API's order, each in the API's own plain words.
function scopeRows(request: AuthorizationRequest): ScopeRow[] {
  const words = new Map(request.scopes.map(({ scope, description }) => [scope, description]));
  return SCOPES.filter(({ scope }) => scope === 'read' || words.has(scope)).map((item) => ({
    ...item,
    description: words.get(item.scope) ?? item.description,
  }));
}

// Sends the decision, then the browser back to the app: its redirect URI with a code, or with
// error=access_denied. The buttons stay off until the browser has left. A session that ended
// meanwhile (401) turns the page into its sign-in.
function useDecision(requestId: string) {
  const queryClient = useQueryClient();
  const decision = useMutation({
    mutationFn: (allow: Scope[] | null) =>
      decideAuthorizationRequest(
        requestId,
        allow === null ? { decision: 'deny' } : { decision: 'allow', scopes: allow },
      ),
    onSuccess: ({ redirect_to }) => window.location.assign(redirect_to),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) queryClient.setQueryData(meQueryOptions().queryKey, null);
    },
  });
  // Back from the app, the browser may restore this page as it was left, decided: offer the
  // buttons again (a decided request then says it has expired).
  const { reset } = decision;
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) reset();
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, [reset]);
  return decision;
}

const problemOf = (error: Error | null) => (error instanceof ApiError ? error.problem : undefined);

// The boxes the user ticks, and what Allow grants: the ticked ones in the API's order, read always.
function useScopeChoice(rows: ScopeRow[]) {
  const [chosen, setChosen] = useState<ReadonlySet<Scope>>(
    () => new Set(rows.map(({ scope }) => scope).filter((scope) => STARTS_ON.has(scope))),
  );
  const isOn = (scope: Scope) => scope === 'read' || chosen.has(scope);
  const toggle = (scope: Scope) =>
    setChosen((current) => {
      const next = new Set(current);
      if (!next.delete(scope)) next.add(scope);
      return next;
    });
  return { isOn, toggle, granted: () => rows.map(({ scope }) => scope).filter(isOn) };
}

type WhoProps = { client: AppClient; me: Me; disabled: boolean; onNotYou: () => void };

// Who vouches for the app's name: the host of the client ID metadata document that names it, else
// nobody, as an app that registered itself picks its own name.
function NamedBy({ documentHost }: { documentHost: string | null }) {
  const Icon = documentHost === null ? Info : ShieldCheck;
  return (
    <p className="mt-1.5 flex items-center justify-center gap-1.5 text-[12.5px] text-muted-foreground">
      <Icon aria-hidden="true" className="size-3.5 flex-none" />
      {documentHost === null ? 'Named by the app itself, not checked' : `Named by ${documentHost}`}
    </p>
  );
}

// .cn-top: the app and CandleStack linked, who asks from where, who names it, and as whom.
function WhoAsks({ client, me, disabled, onNotYou }: WhoProps) {
  return (
    <div className="flex flex-col items-center gap-3.5 text-center">
      <AppPair client={client} />
      <div>
        <h1 className={TITLE}>{client.name} wants access to your CandleStack account</h1>
        <p className={LEAD}>From {client.redirect_host}. It acts as you with what you allow here.</p>
        <NamedBy documentHost={client.document_host} />
      </div>
      <span className="inline-flex flex-wrap items-center justify-center gap-2 text-[13px] text-muted-foreground">
        <Avatar user={me} className="size-5 text-[9px]" />
        Signed in as <b className="font-medium text-foreground">{me.email}</b>
        <button
          type="button"
          className="cursor-pointer text-primary hover:underline hover:underline-offset-2 disabled:cursor-default"
          disabled={disabled}
          onClick={onNotYou}
        >
          Not you?
        </button>
      </span>
    </div>
  );
}

type ChoiceProps = {
  client: AppClient;
  rows: ScopeRow[];
  choice: ReturnType<typeof useScopeChoice>;
  disabled: boolean;
};

// .scs in plain words, then what an app can never do.
function ScopeChoices({ client, rows, choice, disabled }: ChoiceProps) {
  return (
    <>
      <fieldset className="m-0 min-w-0 border-0 p-0">
        <legend className="sr-only">What {client.name} can do</legend>
        <div className="flex flex-col overflow-hidden rounded-lg border">
          {rows.map((item) => (
            <ScopeBox
              key={item.scope}
              {...item}
              plain
              disabled={disabled}
              on={choice.isOn(item.scope)}
              onToggle={() => choice.toggle(item.scope)}
            />
          ))}
        </div>
      </fieldset>
      <p className="flex items-start gap-2 text-[12.5px] text-muted-foreground">
        <Lock aria-hidden="true" className="mt-0.5 size-3.5 flex-none" />
        <span>{client.name} can never delete your account, change how you sign in or download your model files.</span>
      </p>
    </>
  );
}

// Cancel and Allow side by side, 40 px high.
function Decide({ busy, onCancel, onAllow }: { busy: boolean; onCancel: () => void; onAllow: () => void }) {
  return (
    <div className="flex gap-2.5">
      <Button
        type="button"
        variant="outline"
        className={cn('h-10 flex-1', OUTLINE_BUTTON)}
        disabled={busy}
        onClick={onCancel}
      >
        Cancel
      </Button>
      <Button type="button" className={cn('h-10 flex-1', ACCENT_BUTTON)} disabled={busy} onClick={onAllow}>
        Allow
      </Button>
    </div>
  );
}

type Props = { requestId: string; request: AuthorizationRequest; me: Me };

// #consent: who asks, from where, as whom; the scopes in plain words with their boxes, what an app
// can never do, Cancel and Allow, and where the browser goes then. The host is named, as anyone
// can register an app called Claude, and so is the host that names the app, if any. Not you? signs out and shows the sign-in on this page. A
// request that ended meanwhile says so; an account off the beta list gets the sign-in page's words.
export function AllowAccess({ requestId, request, me }: Props) {
  const { client } = request;
  const rows = scopeRows(request);
  const choice = useScopeChoice(rows);
  const decision = useDecision(requestId);
  const switchAccount = useSignOutHere();
  const problem = problemOf(decision.error);
  if (problem === 'authorization-request-not-found') return <ExpiredRequest />;
  if (problem === 'not-on-beta-list')
    return (
      <NotOnBetaList
        client={client}
        email={me.email}
        switching={switchAccount.isPending}
        error={switchAccount.error}
        onUseAnotherAccount={() => switchAccount.mutate()}
      />
    );
  const busy = decision.isPending || decision.isSuccess;
  const error = decision.error ?? switchAccount.error;
  return (
    <ConsentFrame>
      <WhoAsks
        client={client}
        me={me}
        disabled={busy || switchAccount.isPending}
        onNotYou={() => switchAccount.mutate()}
      />
      <ScopeChoices client={client} rows={rows} choice={choice} disabled={busy} />
      {error && <ErrorNote>{error.message}</ErrorNote>}
      <Decide busy={busy} onCancel={() => decision.mutate(null)} onAllow={() => decision.mutate(choice.granted())} />
      <p className={cn(HINT, 'text-center')}>
        You go back to {client.redirect_host}. Disconnect it any time in Settings.
      </p>
    </ConsentFrame>
  );
}
