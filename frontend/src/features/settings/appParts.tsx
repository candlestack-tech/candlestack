import type { ConnectedApp } from '@/api';
import { AppTile } from '@/components/AppTile';
import { cn } from '@/lib/utils';

// The parts of a connected app the MCP card and its Disconnect dialog share
// (design/mockups/api-mcp, appRow and MODALS.disconnect).

// .kr-ic.app: the app's mark on a 34 px neutral tile (on the background inside .who-k).
export function ConnectedAppTile({ app, className }: { app: ConnectedApp; className?: string }) {
  return (
    <AppTile
      client={app.client}
      className={cn('size-[34px] rounded-lg text-[15px]', className)}
      markClassName="size-5"
    />
  );
}

// .kr-n: the app's name, cut short when long, then the host it returns to (anyone can call an app
// Claude, only Claude returns to claude.ai).
export function ConnectedAppName({ app }: { app: ConnectedApp }) {
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <b className="min-w-0 truncate font-medium">{app.client.name}</b>
      <span className="text-[12px] whitespace-nowrap text-muted-foreground">{app.client.redirect_host}</span>
    </span>
  );
}
