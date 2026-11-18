import { build, type Rollup } from 'vite';
import { describe, expect, it } from 'vitest';

import { committedWidget, WIDGET_FILE } from '../../vite.widget.config';

// The backend serves the widget from the file `npm run build:widget` committed into its package
// (backend/src/candlestack/mcp/widget.html): it must be what the sources build now, small, and
// free of anything a chat client's frame would load from the network.

const SIZE_MAX = 150 * 1024;

async function fresh(): Promise<string> {
  const output = (await build({
    configFile: 'vite.widget.config.ts',
    logLevel: 'silent',
    build: { write: false },
  })) as Rollup.RollupOutput;
  const page = output.output.find((item) => item.fileName === WIDGET_FILE);
  if (!page || page.type !== 'asset') throw new Error('The build made no widget page');
  return String(page.source);
}

describe('the widget page', () => {
  it('is what the sources build', async () => {
    expect(committedWidget() === (await fresh()), 'The widget is stale: run npm run build:widget').toBe(true);
  }, 30_000);

  it('is one small file that loads nothing', () => {
    const page = committedWidget();
    expect(page.length).toBeLessThan(SIZE_MAX);
    expect(page).not.toMatch(/<script[^>]+src=|<link[^>]+href=|@import|url\(\s*['"]?(https?:)?\/\//);
  });
});
