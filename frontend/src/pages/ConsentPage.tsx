import { useQuery } from '@tanstack/react-query';
import { useSearch } from '@tanstack/react-router';

import { ApiError, authorizationRequestQueryOptions, meQueryOptions, type AuthorizationRequest, type Me } from '@/api';
import { AllowAccess, ExpiredRequest, LoadFailed, SignInToConnect } from '@/features/consent';
import { SignInLegal } from '@/features/signin';
import { usePageTitle } from '@/lib/usePageTitle';
import { consentPath } from '@/routes/consentSearch';

const SECTION = 'flex flex-1 flex-col items-center justify-center gap-4 py-12 text-sm/[1.5]';

type Query<T> = { data: T | undefined; error: Error | null; isFetching: boolean; refetch: () => unknown };

// What could not be read (no answer, a server error), with Try again; null when all came.
function loadFailure(...queries: Query<unknown>[]) {
  const failed = queries.find((query) => query.error !== null);
  if (failed === undefined) return null;
  return (
    <LoadFailed
      message={failed.error?.message ?? ''}
      retrying={failed.isFetching}
      onRetry={() => void failed.refetch()}
    />
  );
}

// The card for the request and the session: expired or unknown, failed to load, still loading
// (null), the sign-in, or the request itself.
function consentCard(requestId: string | undefined, request: Query<AuthorizationRequest>, me: Query<Me | null>) {
  if (requestId === undefined || (request.error instanceof ApiError && request.error.status === 404))
    return <ExpiredRequest />;
  const failure = loadFailure(request, me);
  if (failure) return failure;
  if (request.data === undefined || me.data === undefined) return null;
  if (me.data === null) return <SignInToConnect client={request.data.client} next={consentPath(requestId)} />;
  return <AllowAccess key={me.data.id} requestId={requestId} request={request.data} me={me.data} />;
}

// /oauth/consent?request=<id> (design/mockups/api-mcp, #consent): an app asks for access to the
// account. The request names the app without a session, so a signed-out visitor sees which app
// asks and signs in on the same card, coming back here. A request that was decided, expired or
// never was, and an address without one, say so.
export function ConsentPage() {
  usePageTitle('Allow access');
  const { request: requestId } = useSearch({ from: '/_public/oauth/consent' });
  const me = useQuery(meQueryOptions());
  const request = useQuery({
    ...authorizationRequestQueryOptions(requestId ?? ''),
    enabled: requestId !== undefined,
  });
  const card = consentCard(requestId, request, me);
  return (
    <section aria-label="Allow access" aria-busy={card === null} className={SECTION}>
      {card}
      {card?.type === SignInToConnect && <SignInLegal />}
    </section>
  );
}
