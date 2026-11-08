import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';

import { authorizationRequestQueryOptions, deniedSignInQueryOptions } from '@/api';
import { NotOnBetaList } from '@/features/consent';
import { DeniedSignIn, SignInCard, SignInLegal, StartSignIn } from '@/features/signin';
import { usePageTitle } from '@/lib/usePageTitle';
import { consentRequestOf } from '@/routes/consentSearch';

const SECTION = 'flex flex-1 flex-col items-center justify-center gap-4 py-12 text-sm/[1.5]';

// /signin?next=/backtests&error=denied, for Sign in and Try Backtest alike (mockups/home,
// #/signin). After a denied sign-in it names the account the beta list turned away (the route
// has loaded it); without that answer, and after a failed one, it simply offers the buttons. A
// sign-in that started on an app's consent page (`next`) gets that page's card naming the app, or
// the plain one when the request is gone.
export function SignInPage() {
  usePageTitle('Sign in');
  const { next, error } = useSearch({ from: '/_public/signin' });
  const navigate = useNavigate({ from: '/signin' });
  const denied = useQuery({ ...deniedSignInQueryOptions(), enabled: error === 'denied' });
  const email = error === 'denied' ? denied.data?.email : undefined;
  const requestId = error === 'denied' ? consentRequestOf(next) : undefined;
  const request = useQuery({
    ...authorizationRequestQueryOptions(requestId ?? ''),
    enabled: requestId !== undefined,
  });
  const tryAnotherAccount = () => void navigate({ search: { next }, replace: true });

  if (email !== undefined && requestId !== undefined && request.data !== undefined)
    return (
      <section aria-label="Sign in" className={SECTION}>
        <NotOnBetaList
          client={request.data.client}
          email={email}
          switching={false}
          error={null}
          onUseAnotherAccount={tryAnotherAccount}
        />
      </section>
    );
  return (
    <section aria-label="Sign in" className={SECTION}>
      <SignInCard>
        {email === undefined ? (
          <StartSignIn next={next} />
        ) : (
          <DeniedSignIn email={email} onUseAnotherAccount={tryAnotherAccount} />
        )}
      </SignInCard>
      <SignInLegal />
    </section>
  );
}
