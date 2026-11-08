import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { connectedAppsQueryOptions, disconnectApp, type ConnectedApp } from '@/api';

export const useConnectedApps = () => useQuery(connectedAppsQueryOptions());

// Disconnects an app: it leaves the list, its tokens stop working at once.
export function useDisconnectApp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (app: ConnectedApp) => disconnectApp(app.id),
    onSuccess: (_answer, app) =>
      queryClient.setQueryData(
        connectedAppsQueryOptions().queryKey,
        (apps) => apps && { ...apps, items: apps.items.filter((item) => item.id !== app.id) },
      ),
  });
}
