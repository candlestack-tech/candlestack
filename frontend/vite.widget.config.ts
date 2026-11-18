import { readFileSync } from 'node:fs';
import path from 'node:path';
import { defineConfig, type Plugin } from 'vite';

// The chat widget of the MCP server (src/widget/): one HTML file with its script and styles inline,
// which the server serves as its ui:// resource. A chat client shows it in a sandboxed frame that
// loads nothing from the network, so nothing may stay outside the file. `npm run build:widget`
// writes it into the backend package; src/widget/build.test.ts checks the committed file is current.

export const WIDGET_OUT = path.resolve(import.meta.dirname, '../backend/src/candlestack/mcp');
export const WIDGET_FILE = 'widget.html';

// A closing tag inside the inlined code would end the element early.
const escaped = (code: string, tag: 'script' | 'style') => code.replaceAll(`</${tag}`, `<\\/${tag}`);

function inlineEverything(): Plugin {
  return {
    name: 'candlestack-inline-widget',
    enforce: 'post',
    generateBundle(_options, bundle) {
      const page = Object.values(bundle).find((item) => item.type === 'asset' && item.fileName.endsWith('.html'));
      if (!page || page.type !== 'asset') throw new Error('The widget build made no HTML page');
      let html = String(page.source);
      for (const [name, item] of Object.entries(bundle)) {
        if (item === page) continue;
        if (item.type === 'chunk') {
          const tag = new RegExp(`<script[^>]*src="[^"]*${item.fileName}"[^>]*></script>`);
          html = html.replace(tag, () => `<script type="module">${escaped(item.code, 'script')}</script>`);
        } else if (item.fileName.endsWith('.css')) {
          const tag = new RegExp(`<link[^>]*href="[^"]*${item.fileName}"[^>]*>`);
          html = html.replace(tag, () => `<style>${escaped(String(item.source), 'style')}</style>`);
        } else {
          throw new Error(`The widget may not load a file of its own: ${item.fileName}`);
        }
        delete bundle[name];
      }
      page.fileName = WIDGET_FILE;
      page.source = html;
    },
  };
}

export default defineConfig({
  root: path.resolve(import.meta.dirname, 'src/widget'),
  base: './',
  // The app's public/ (favicons, theme.js) is not the widget's.
  publicDir: false,
  plugins: [inlineEverything()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  build: {
    outDir: WIDGET_OUT,
    // The folder is the backend's MCP package: only the page is written into it.
    emptyOutDir: false,
    target: 'es2022',
    modulePreload: false,
    cssCodeSplit: false,
    assetsInlineLimit: Number.POSITIVE_INFINITY,
    reportCompressedSize: false,
    rollupOptions: { input: path.resolve(import.meta.dirname, 'src/widget/widget.html') },
  },
});

// For the test that compares the committed page with a fresh build.
export const committedWidget = () => readFileSync(path.join(WIDGET_OUT, WIDGET_FILE), 'utf8');
