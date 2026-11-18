import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Footer } from '@/components/Footer';

// The anchor whose text is `name`, with its attributes in any order.
function link(html: string, name: string): string {
  const attributes = new RegExp(`<a ([^>]*)>${name}</a>`).exec(html)?.[1];
  if (attributes === undefined) throw new Error(`no link "${name}"`);
  return attributes;
}

describe('Footer', () => {
  it('links the API docs of its own host, the status page and the website in a new tab', () => {
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain('<span>CandleStack</span>');
    expect(link(html, 'API docs')).toContain('href="/api/v1/docs"');
    expect(link(html, 'Status')).toContain('href="https://ops.candlestack.tech"');
    expect(link(html, 'candlestack.tech')).toContain('href="https://candlestack.tech"');
    for (const name of ['API docs', 'Status', 'candlestack.tech']) {
      expect(link(html, name)).toContain('target="_blank"');
      expect(link(html, name)).toContain('rel="noopener"');
    }
  });

  // #166: the chart has no TradingView logo, so the footer carries the attributions.
  it('credits TradingView Lightweight Charts', () => {
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain('Charts by');
    expect(link(html, 'TradingView Lightweight Charts™')).toContain('href="https://www.tradingview.com/"');
  });

  it('credits Logo.dev for the instrument logos', () => {
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain('Logos provided by');
    expect(link(html, 'Logo.dev')).toContain('href="https://logo.dev"');
  });

  it('opens the credits in a new tab without an opener', () => {
    const html = renderToStaticMarkup(<Footer />);
    for (const name of ['TradingView Lightweight Charts™', 'Logo.dev']) {
      expect(link(html, name)).toContain('target="_blank"');
      expect(link(html, name)).toContain('rel="noreferrer"');
    }
  });

  // #465: 24 px high for a finger (WCAG 2.5.8), the padding taking no room from the line.
  it('gives every link a target 24 px high', () => {
    const html = renderToStaticMarkup(<Footer />);
    for (const name of ['API docs', 'Status', 'candlestack.tech'])
      expect(link(html, name)).toContain('-my-[3px] py-[3px]');
    for (const name of ['TradingView Lightweight Charts™', 'Logo.dev']) expect(link(html, name)).toContain('py-[3px]');
  });
});
