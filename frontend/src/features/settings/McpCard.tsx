import { ExternalLink } from 'lucide-react';
import { useState, type ComponentType } from 'react';

import type { ConnectedApp } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { ClaudeLogo, CursorLogo, OpenAILogo, VSCodeLogo } from '@/components/clientLogos';
import { DashboardCard } from '@/components/DashboardCard';
import { HINT } from '@/components/Hint';
import { PlugIcon } from '@/components/icons';
import { Button } from '@/components/ui/button';
import { useDateStyle } from '@/features/preferences';
import { formatDate } from '@/lib/dates';
import { useNow } from '@/lib/useNow';
import { cn } from '@/lib/utils';

import { lastUsedText } from './apiKeyText';
import { ConnectedAppName, ConnectedAppTile } from './appParts';
import { DisconnectAppDialog } from './DisconnectAppDialog';
import { CopyButton, LoadError, ScopeChips } from './keyParts';
import { Code, SettingsLead, SettingsRow } from './rows';
import { useConnectedApps } from './useConnectedApps';

// The install guide of every client, step by step (website, docs/mcp/install).
const MCP_INSTALL_URL = 'https://candlestack.tech/docs/mcp/install/';

type Client = {
  name: string;
  how: string;
  Logo: ComponentType<{ className?: string }> | null;
  // The client's section of the guide, when the link goes straight to it.
  anchor?: string;
};

// The clients the guide covers, each with its real mark, and any other over Streamable HTTP. Claude,
// ChatGPT and Codex sign in with OAuth and show under Connected apps, Claude Code with OAuth or an
// API key; the others use an API key. Codex has no mark of its own on openai.com/brand: the Blossom.
const CLIENTS: Client[] = [
  { name: 'Claude', how: 'Add a custom connector, sign in with CandleStack', Logo: ClaudeLogo },
  { name: 'ChatGPT', how: 'Add a connector, sign in with CandleStack', Logo: OpenAILogo },
  { name: 'Codex', how: 'One command, sign in with codex mcp login', Logo: OpenAILogo, anchor: 'codex' },
  { name: 'Claude Code', how: 'One command, sign in with CandleStack or use an API key', Logo: ClaudeLogo },
  { name: 'Cursor', how: 'One entry in mcp.json with an API key', Logo: CursorLogo },
  { name: 'VS Code', how: 'One entry in mcp.json with an API key', Logo: VSCodeLogo },
  { name: 'Other clients', how: 'Any MCP client over Streamable HTTP', Logo: null },
];

// .cl: one line per client, its mark on a 22 px neutral tile (a + for the others), its name and
// how it connects, and Guide to the install docs.
function ClientLines() {
  return (
    <div className="flex flex-col">
      {CLIENTS.map(({ name, how, Logo, anchor }) => (
        <div
          key={name}
          className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-2.5 py-1.5 [&+&]:border-t [&+&]:border-dashed"
        >
          <span
            aria-hidden="true"
            className="grid size-[22px] place-items-center rounded-md bg-surface-2 text-[11px] font-bold"
          >
            {Logo ? <Logo /> : '+'}
          </span>
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-[13.5px]">
            <b className="font-medium">{name}</b>
            <span className="text-[12.5px] text-muted-foreground">{how}</span>
          </span>
          <a
            href={anchor === undefined ? MCP_INSTALL_URL : `${MCP_INSTALL_URL}#${anchor}`}
            target="_blank"
            rel="noopener"
            aria-label={`Guide for ${name}`}
            className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline hover:underline-offset-2"
          >
            Guide
            <ExternalLink aria-hidden="true" className="size-3" />
          </a>
        </div>
      ))}
    </div>
  );
}

// .kr-t: when the app last called the server, over when it was connected.
function AppTimes({ app, now }: { app: ConnectedApp; now: number }) {
  const style = useDateStyle();
  const used = lastUsedText(app, style, now);
  return (
    <div className="flex min-w-0 flex-col gap-px text-[12.5px] max-[640px]:col-[2/4] max-[640px]:row-2">
      <span>{used === null ? <span className="text-muted-foreground">Never used</span> : `Last used ${used}`}</span>
      <span className="text-[11.5px] text-muted-foreground">
        Connected {formatDate(new Date(app.connected_at), style)}
      </span>
    </div>
  );
}

// .kr: the app's tile, its name and host over its scopes, its times, and Disconnect. On a phone the
// times go under the name, as in the keys card.
function AppRow({ app, now, onDisconnect }: { app: ConnectedApp; now: number; onDisconnect: () => void }) {
  return (
    <div
      className={cn(
        'grid grid-cols-[34px_minmax(0,1fr)_minmax(0,200px)_auto] items-center gap-3 rounded-lg border px-3 py-2.5',
        'max-[640px]:grid-cols-[34px_minmax(0,1fr)_auto] max-[640px]:gap-y-1.5',
      )}
    >
      <ConnectedAppTile app={app} />
      <div className="flex min-w-0 flex-col gap-1">
        <ConnectedAppName app={app} />
        <ScopeChips scopes={app.scopes} />
      </div>
      <AppTimes app={app} now={now} />
      <Button
        type="button"
        variant="outline"
        aria-label={`Disconnect ${app.client.name}`}
        className={cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'max-[640px]:col-3 max-[640px]:row-1')}
        onClick={onDisconnect}
      >
        Disconnect
      </Button>
    </div>
  );
}

// .st-empty.sm: no app signed in yet, and how one does.
function NoApps() {
  return (
    <div className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 py-[18px] text-center">
      <b className="text-[14.5px] font-semibold">No apps connected</b>
      <span className={HINT}>
        Add the server URL as a connector in Claude or ChatGPT. You sign in and choose what it can do.
      </span>
    </div>
  );
}

// .st-sub: the list's title with how many, and what it holds.
function AppsHead({ count }: { count: number | undefined }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-2.5 border-t pt-3.5 pb-2">
      <h3 className="inline-flex items-baseline gap-2 text-sm font-semibold">
        Connected apps
        {count !== undefined && (
          <span className="font-mono text-[12px] font-normal text-muted-foreground">{count}</span>
        )}
      </h3>
      <span className={HINT}>Apps that signed in with your account. Keys are in the card above.</span>
    </div>
  );
}

// The apps the user allowed, the most recently connected first, each with Disconnect (it asks
// first). The time moves every minute, so the last use stays right while Settings is open.
function ConnectedApps() {
  const { data: apps, error, isFetching, refetch } = useConnectedApps();
  const now = useNow(true, 60_000);
  const [leaving, setLeaving] = useState<{ app: ConnectedApp | null; open: boolean }>({ app: null, open: false });
  return (
    <>
      <AppsHead count={apps?.items.length} />
      {error && <LoadError error={error} retrying={isFetching} onRetry={() => void refetch()} />}
      {apps !== undefined && apps.items.length > 0 && (
        <div className="flex flex-col gap-2">
          {apps.items.map((app) => (
            <AppRow key={app.id} app={app} now={now} onDisconnect={() => setLeaving({ app, open: true })} />
          ))}
        </div>
      )}
      {apps?.items.length === 0 && <NoApps />}
      <DisconnectAppDialog
        app={leaving.app}
        open={leaving.open}
        onOpenChange={(open) => setLeaving((state) => ({ ...state, open }))}
      />
    </>
  );
}

// The MCP server card (design/mockups/api-mcp, mcpCard): the server's address with Copy, how each
// client connects with its guide, and the apps that signed in with OAuth. The address is this
// host's, so stage shows stage's.
export function McpCard() {
  const url = `${window.location.origin}/mcp`;
  return (
    <DashboardCard icon={PlugIcon} title="MCP server">
      <SettingsLead>
        Use CandleStack from Claude, ChatGPT, Cursor or any MCP client. Ask about your backtests, start new ones and
        compare them in a chat.
      </SettingsLead>
      <SettingsRow label="Server URL">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate">
            <Code>{url}</Code>
          </span>
          <CopyButton text={url} />
        </div>
      </SettingsRow>
      <SettingsRow label="Connect" hint="Step by step for each app" top>
        <ClientLines />
      </SettingsRow>
      <ConnectedApps />
    </DashboardCard>
  );
}
