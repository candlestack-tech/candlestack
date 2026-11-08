import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { AppClient } from '@/api';
import { AppTile } from '@/components/AppTile';

const CLAUDE: AppClient = {
  name: 'Claude',
  logo_uri: null,
  client_uri: 'https://claude.ai',
  redirect_host: 'claude.ai',
  document_host: 'claude.ai',
};

const tile = (client: AppClient) => renderToStaticMarkup(<AppTile client={client} />);
const CLAUDE_CLAY = 'fill="#D97757"';

// A known app's mark goes by the host that vouches for it, so a lookalike cannot borrow it.
describe('AppTile', () => {
  it("draws a known app's mark when its document sits on the app's host", () => {
    expect(tile(CLAUDE)).toContain(CLAUDE_CLAY);
  });

  it('draws no mark for a document elsewhere, even one that returns to the known host', () => {
    for (const document_host of ['claude.ai.example', 'example.com']) {
      const html = tile({ ...CLAUDE, document_host });
      expect(html).not.toContain('<svg');
      expect(html).toContain('>C</span>');
    }
  });

  it('goes by the host an app that registered itself returns to', () => {
    expect(tile({ ...CLAUDE, name: 'ChatGPT', redirect_host: 'chatgpt.com', document_host: null })).toContain('<svg');
    expect(tile({ ...CLAUDE, redirect_host: 'claude.ai.example', document_host: null })).not.toContain('<svg');
  });

  it('shows the logo of an app named by a document, never that of an app that registered itself', () => {
    const logo = 'https://notes.example/logo.png';
    const notes = { ...CLAUDE, name: 'Notes', logo_uri: logo, redirect_host: 'notes.example' };
    expect(tile({ ...notes, document_host: 'notes.example' })).toContain(`src="${logo}"`);
    const own = tile({ ...notes, document_host: null });
    expect(own).not.toContain('<img');
    expect(own).toContain('>N</span>');
  });

  it('takes no mark from the names every object has', () => {
    const html = tile({ ...CLAUDE, name: 'Proto', redirect_host: 'constructor', document_host: null });
    expect(html).toContain('>P</span>');
  });
});
