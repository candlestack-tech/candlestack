// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { InstrumentSymbol } from '@/components/InstrumentSymbol';

afterEach(cleanup);

const classes = (element: Element | null | undefined) => element?.getAttribute('class')?.split(' ') ?? [];

// #318: one piece names an instrument everywhere in the app: its icon, the symbol, maybe the name.
describe('InstrumentSymbol', () => {
  it('shows the icon of the instrument before its symbol', () => {
    const { container } = render(<InstrumentSymbol id="stock:BRK.B" symbol="BRK.B" size={20} />);
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe('/api/v1/data/instruments/stock%3ABRK.B/icon');
    expect(img?.getAttribute('loading')).toBe('lazy');
    expect(img?.getAttribute('alt')).toBe('');
    expect(container.textContent).toBe('BRK.B');
    expect(classes(screen.getByText('BRK.B'))).toContain('font-mono');
  });

  it('draws the icon at the size of the place, on the white tile', () => {
    for (const [size, box, gap] of [
      [16, 'size-4', 'gap-1.5'],
      [20, 'size-5', 'gap-1.5'],
      [24, 'size-6', 'gap-2'],
    ] as const) {
      const { container } = render(<InstrumentSymbol id="crypto:BTCUSDT" symbol="BTCUSDT" size={size} />);
      const img = container.querySelector('img');
      expect([img?.getAttribute('width'), img?.getAttribute('height')]).toEqual([String(size), String(size)]);
      expect(classes(img?.parentElement)).toEqual(expect.arrayContaining([box, 'bg-white', 'rounded-[10%]']));
      expect(classes(container.firstElementChild)).toContain(gap);
      cleanup();
    }
  });

  it("draws the backend's fallback in its size when the image does not load", () => {
    const { container } = render(<InstrumentSymbol id="stock:ZZZZ" symbol="ZZZZ" size={20} />);
    fireEvent.error(container.querySelector('img')!);
    expect(container.querySelector('img')).toBeNull();
    const fallback = container.querySelector('.bg-icon-fallback');
    expect(fallback?.querySelector('.lucide-chart-candlestick')).not.toBeNull();
    expect(classes(fallback?.parentElement)).toContain('size-5');
    expect(screen.getByText('ZZZZ')).toBeDefined();
  });

  it('cuts a symbol alone with an ellipsis, the whole one in its title', () => {
    render(<InstrumentSymbol id="crypto:1000SATSUSDT" symbol="1000SATSUSDT" size={20} />);
    const symbol = screen.getByText('1000SATSUSDT');
    expect(symbol.getAttribute('title')).toBe('1000SATSUSDT');
    expect(classes(symbol)).toEqual(expect.arrayContaining(['truncate', 'min-w-0']));
  });

  it('keeps the symbol whole beside the name and cuts the name instead', () => {
    render(<InstrumentSymbol id="stock:TSLA" symbol="TSLA" name="Tesla, Inc. Common Stock" size={24} />);
    expect(classes(screen.getByText('TSLA'))).toContain('shrink-0');
    const name = screen.getByText('Tesla, Inc. Common Stock');
    expect(name.getAttribute('title')).toBe('Tesla, Inc. Common Stock');
    expect(classes(name)).toEqual(expect.arrayContaining(['truncate', 'min-w-0', 'text-muted-foreground']));
  });
});
