// How the widget talks to the chat client that shows it. Claude and ChatGPT both speak MCP Apps
// (modelcontextprotocol/ext-apps, spec 2026-01-26): JSON-RPC 2.0 over postMessage with the frame's
// parent. The widget asks `ui/initialize`, says `ui/notifications/initialized`, and then gets the
// tool's result (`ui/notifications/tool-result`, a CallToolResult) and the theme. A host that only
// has ChatGPT's older `window.openai` is used through that instead, found by feature, not by name.

export type Theme = 'light' | 'dark';

export type ToolResult = {
  structuredContent?: Record<string, unknown>;
  _meta?: Record<string, unknown>;
  isError?: boolean;
};

export type Host = {
  // Calls `listener` with each result the host sends, and with the one it already has.
  onResult(listener: (result: ToolResult) => void): void;
  onTheme(listener: (theme: Theme) => void): void;
  // Asks the host to open `url` in the browser.
  openLink(url: string): void;
  // Calls a tool of the server through the host, which signs the call in: the widget has no key.
  callTool(name: string, args: Record<string, unknown>): Promise<ToolResult>;
  // Tells the host how tall the widget is, so its frame fits it.
  resized(height: number): void;
};

export const PROTOCOL_VERSION = '2026-01-26';
const APP_INFO = { name: 'CandleStack', version: '1' };
// How long the widget waits for an MCP Apps host before it tries window.openai.
const HANDSHAKE_MS = 1500;

type Message = {
  jsonrpc?: string;
  id?: number | string;
  method?: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: { message?: string };
};

type Listeners = { results: ((result: ToolResult) => void)[]; themes: ((theme: Theme) => void)[] };

const themeOf = (value: unknown): Theme | null => (value === 'light' || value === 'dark' ? value : null);

const contextTheme = (context: unknown) => themeOf((context as { theme?: unknown } | undefined)?.theme ?? null);

// A link the host would not open still opens from the frame, where its sandbox lets it.
function openHere(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer');
}

class McpAppsHost implements Host {
  private next = 1;
  private readonly pending = new Map<number | string, (message: Message) => void>();
  private readonly listeners: Listeners = { results: [], themes: [] };
  private last: ToolResult | null = null;
  private readonly target: Window;

  constructor(target: Window) {
    this.target = target;
    window.addEventListener('message', (event: MessageEvent<Message>) => {
      if (event.source === this.target && event.data?.jsonrpc === '2.0') this.receive(event.data);
    });
  }

  private send(message: Message) {
    this.target.postMessage({ jsonrpc: '2.0', ...message }, '*');
  }

  request(method: string, params: Record<string, unknown>): Promise<Record<string, unknown>> {
    const id = this.next++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, ({ result, error }) => {
        if (error) reject(new Error(error.message ?? method));
        else resolve(result ?? {});
      });
      this.send({ id, method, params });
    });
  }

  private notify(method: string, params: Record<string, unknown>) {
    this.send({ method, params });
  }

  private receive(message: Message) {
    if (message.method === undefined) {
      if (message.id !== undefined) this.pending.get(message.id)?.(message);
      if (message.id !== undefined) this.pending.delete(message.id);
      return;
    }
    const params = message.params ?? {};
    if (message.method === 'ui/notifications/tool-result') this.result(params);
    if (message.method === 'ui/notifications/host-context-changed') this.theme(contextTheme(params));
    // Any request of the host (ui/resource-teardown, ping) is answered: the widget keeps no state.
    if (message.id !== undefined) this.send({ id: message.id, result: {} });
  }

  private result(result: ToolResult) {
    this.last = result;
    this.listeners.results.forEach((listener) => listener(result));
  }

  theme(theme: Theme | null) {
    if (theme) this.listeners.themes.forEach((listener) => listener(theme));
  }

  async connect(): Promise<Theme | null> {
    const answer = await this.request('ui/initialize', {
      protocolVersion: PROTOCOL_VERSION,
      appInfo: APP_INFO,
      appCapabilities: { availableDisplayModes: ['inline'] },
    });
    this.notify('ui/notifications/initialized', {});
    return contextTheme(answer.hostContext);
  }

  onResult(listener: (result: ToolResult) => void) {
    this.listeners.results.push(listener);
    if (this.last) listener(this.last);
  }

  onTheme(listener: (theme: Theme) => void) {
    this.listeners.themes.push(listener);
  }

  openLink(url: string) {
    this.request('ui/open-link', { url }).catch(() => openHere(url));
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<ToolResult> {
    return this.request('tools/call', { name, arguments: args });
  }

  resized(height: number) {
    this.notify('ui/notifications/size-changed', { height });
  }
}

// ChatGPT's own API, before it spoke MCP Apps: the result's structured content and _meta are
// globals, and a change of them is the `openai:set_globals` event.
type OpenAi = {
  theme?: unknown;
  toolOutput?: Record<string, unknown> | null;
  toolResponseMetadata?: Record<string, unknown> | null;
  openExternal?: (options: { href: string }) => void;
  callTool?: (name: string, args: Record<string, unknown>) => Promise<ToolResult>;
  notifyIntrinsicHeight?: (height: number) => void;
};

const openAi = () => (window as { openai?: OpenAi }).openai;

function openAiHost(api: OpenAi): Host {
  const current = (): ToolResult | null =>
    api.toolOutput ? { structuredContent: api.toolOutput, _meta: api.toolResponseMetadata ?? undefined } : null;
  return {
    onResult(listener) {
      // The event says any global changed (the theme, the height): only a new output is drawn.
      let drawn: unknown = null;
      const send = () => {
        const shown = current();
        if (shown && api.toolOutput !== drawn) {
          drawn = api.toolOutput;
          listener(shown);
        }
      };
      send();
      window.addEventListener('openai:set_globals', send);
    },
    onTheme(listener) {
      const send = () => {
        const theme = themeOf(api.theme);
        if (theme) listener(theme);
      };
      send();
      window.addEventListener('openai:set_globals', send);
    },
    openLink: (url) => (api.openExternal ? api.openExternal({ href: url }) : openHere(url)),
    callTool: (name, args) =>
      api.callTool ? api.callTool(name, args) : Promise.reject(new Error('The host calls no tools')),
    resized: (height) => api.notifyIntrinsicHeight?.(height),
  };
}

const timeout = (ms: number) =>
  new Promise<never>((_resolve, reject) => {
    setTimeout(() => reject(new Error('No MCP Apps host answered')), ms);
  });

// The host of the widget and the theme it starts with; null outside a chat client's frame.
export async function connect(target: Window = window.parent): Promise<{ host: Host; theme: Theme | null } | null> {
  if (target === window) return null;
  const apps = new McpAppsHost(target);
  try {
    const theme = await Promise.race([apps.connect(), timeout(HANDSHAKE_MS)]);
    return { host: apps, theme };
  } catch {
    const api = openAi();
    return api ? { host: openAiHost(api), theme: themeOf(api.theme) } : { host: apps, theme: null };
  }
}
