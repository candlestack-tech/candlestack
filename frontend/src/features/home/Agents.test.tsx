import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Agents } from './Agents';

const html = () => renderToString(<Agents />);
// The anchor whose text starts with `name`, with its attributes in any order.
const link = (name: string) => new RegExp(`<a ([^>]*)>${name}`).exec(html())?.[1] ?? '';

describe('Agents', () => {
  it('is the section the hero links to', () => {
    expect(html()).toMatch(/<section [^>]*id="agents"/);
    expect(html()).toMatch(/<h2 [^>]*>Let your AI agent run the backtests<\/h2>/);
  });

  it('names the clients an agent can connect from, each by its logo', () => {
    const list = /<ul [^>]*aria-label="Supported clients"[^>]*>(.*?)<\/ul>/.exec(html())?.[1] ?? '';
    const clients = [...list.matchAll(/<\/svg>([^<]*)<\/li>/g)].map((match) => match[1]);
    expect(clients).toEqual(['Claude Code', 'claude.ai', 'ChatGPT', 'Codex', 'Cursor', 'VS Code']);
  });

  it('opens the install guide on the website and the API docs of its own host in a new tab', () => {
    expect(link('Install the server')).toContain('href="https://candlestack.tech/docs/mcp/install/"');
    expect(link('API reference')).toContain('href="/api/v1/docs"');
    for (const name of ['Install the server', 'API reference']) {
      expect(link(name)).toContain('target="_blank"');
      expect(link(name)).toContain('rel="noopener"');
    }
  });

  it('shows a sample session with the server tools, marked as an example', () => {
    const tools = [...html().matchAll(/<code [^>]*>([^<]*)<\/code>/g)].map((match) => match[1]);
    expect(tools).toEqual(['start_backtest', 'start_backtest', 'wait_for_backtest', 'compare_backtests']);
    expect(html()).toContain('>EXAMPLE</span>');
    expect(html()).toContain('Sample session, not market data');
  });

  it('marks the better backtest best in all three columns and the other worst', () => {
    expect(html()).toMatch(/<table [^>]*aria-label="Example comparison"/);
    const rows = [...html().matchAll(/<tr[^>]*>(.*?)<\/tr>/g)].map((match) => match[1] ?? '');
    expect(rows[1]).toContain('lstm-kagi');
    expect(rows[1]?.match(/var\(--up\)_14%/g)).toHaveLength(3);
    expect(rows[2]?.match(/text-down/g)).toHaveLength(3);
  });

  it('links nothing from inside the sample', () => {
    const chat = html().split('Claude · candlestack')[1] ?? '';
    expect(chat).toContain('Open the comparison in CandleStack');
    expect(chat).not.toContain('<a ');
  });
});
