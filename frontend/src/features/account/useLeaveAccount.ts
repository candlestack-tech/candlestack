import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';

import { deleteMe, meQueryOptions, signOut, signOutEverywhere } from '@/api';

// Ends the session through `request`, forgets the user and their data and goes to the home page,
// or with `next` to the sign-in page, which returns there.
// The user is forgotten first: the signed-in layout shows nothing without one, so no page of the
// app is left on screen meanwhile. When the API cannot do it (no answer, a server error), the
// session is still open: the user stays signed in on the page and can try again.
function useLeaveAccount(request: () => Promise<void>, next?: string) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: request,
    onSuccess: async () => {
      const me = meQueryOptions().queryKey;
      queryClient.setQueryData(me, null);
      queryClient.removeQueries({ queryKey: ['account'], predicate: (query) => query.queryKey[1] !== me[1] });
      await (next === undefined ? navigate({ to: '/' }) : navigate({ to: '/signin', search: { next } }));
    },
  });
}

// Sign out in the avatar menu: this browser's session.
export const useSignOut = () => useLeaveAccount(signOut);

// Use another account (the upload page of a link another account made): sign out, then sign in
// with another one, which returns to `next`.
export const useSwitchAccount = (next: string) => useLeaveAccount(signOut, next);

// Settings, Account: every session of the user, on every device.
export const useSignOutEverywhere = () => useLeaveAccount(signOutEverywhere);

// Settings, Account: the account with everything it owns.
export const useDeleteAccount = () => useLeaveAccount(deleteMe);
