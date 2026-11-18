// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CandleChart } from './CandleChart';

type Options = {
  localization?: { timeFormatter?: (time: number) => string };
  timeScale?: { fixLeftEdge?: boolean; minBarSpacing?: number; maxBarSpacing?: number };
};
type SeriesOptions = {
  lastValueVisible?: boolean;
  priceLineVisible?: boolean;
  priceFormat?: { type: string; formatter?: (price: number) => string };
};

// The chart draws on a canvas, which jsdom has not: a stand-in keeps what the chart was told. Its
// time scale is `width` pixels wide and the view `position` bars past the newest candle.
const options = vi.hoisted(() => [] as Options[]);
const series = vi.hoisted(() => [] as { kind: string; options: SeriesOptions[] }[]);
const timeScale = vi.hoisted(() => ({
  size: { width: 0, position: 0 },
  onRange: [] as ((range: { from: number; to: number } | null) => void)[],
  onSize: [] as (() => void)[],
  subscribeVisibleLogicalRangeChange(handler: (range: { from: number; to: number } | null) => void) {
    this.onRange.push(handler);
  },
  subscribeSizeChange(handler: () => void) {
    this.onSize.push(handler);
  },
  width() {
    return this.size.width;
  },
  scrollPosition() {
    return this.size.position;
  },
  getVisibleLogicalRange: () => null,
  setVisibleLogicalRange: vi.fn(),
  scrollToPosition: vi.fn(),
  scrollToRealTime: vi.fn(),
}));

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'Candlestick',
  HistogramSeries: 'Histogram',
  ColorType: { Solid: 'solid' },
  createChart: (_: HTMLElement, created: Options) => {
    options.push(created);
    return {
      applyOptions: (next: Options) => options.push(next),
      addSeries: (kind: string, first: SeriesOptions) => {
        const added = { kind, options: [first] };
        series.push(added);
        return {
          applyOptions: (next: SeriesOptions) => added.options.push(next),
          setData: vi.fn(),
          priceScale: () => ({ applyOptions: vi.fn() }),
        };
      },
      timeScale: () => timeScale,
      subscribeCrosshairMove: vi.fn(),
      remove: vi.fn(),
    };
  },
}));

afterEach(() => {
  cleanup();
  options.length = 0;
  series.length = 0;
  timeScale.size = { width: 0, position: 0 };
  timeScale.onRange.length = 0;
  timeScale.onSize.length = 0;
  timeScale.setVisibleLogicalRange.mockClear();
  timeScale.scrollToPosition.mockClear();
});

// 2024-06-03 13:30 UTC, the NYSE open in summer time.
const SUMMER_OPEN = Date.UTC(2024, 5, 3, 13, 30) / 1000;
const BAR = { time: SUMMER_OPEN, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 };
const BARS = [BAR];
const STYLE = { timeZone: 'UTC', dateFormat: 'dmy', timeFormat: '24' } as const;

// What the crosshair shows now: the last time formatter the chart was given.
function crosshairText(): string | undefined {
  return options.findLast((option) => option.localization?.timeFormatter)?.localization?.timeFormatter?.(SUMMER_OPEN);
}

describe('the candle chart', () => {
  it("hides the library's canvases from a screen reader but keeps the legend", () => {
    const { container } = render(
      <CandleChart bars={BARS} market="stock" dateStyle={STYLE} daily={false} onNearLeftEdge={() => undefined} />,
    );
    expect(container.querySelector('.absolute.inset-0')?.getAttribute('aria-hidden')).toBe('true');
    const legend = container.querySelector('.font-mono');
    expect(legend?.textContent).toContain('C 1.5000');
    expect(legend?.closest('[aria-hidden]')).toBeNull();
  });

  it("writes the crosshair's date and time in the user's zone and formats, and follows a change", () => {
    const chart = (timeZone: string, dateFormat: 'dmy' | 'iso', timeFormat: '24' | '12') => (
      <CandleChart
        bars={BARS}
        market="stock"
        dateStyle={{ timeZone, dateFormat, timeFormat }}
        daily={false}
        onNearLeftEdge={() => undefined}
      />
    );
    const { rerender } = render(chart('America/New_York', 'dmy', '24'));
    expect(crosshairText()).toBe('3 Jun 2024, 09:30');

    rerender(chart('America/New_York', 'iso', '12'));
    expect(crosshairText()).toBe('2024-06-03, 9:30 AM');

    rerender(chart('Europe/Bratislava', 'iso', '12'));
    expect(crosshairText()).toBe('2024-06-03, 3:30 PM');
  });

  it('gives a chart of days the date alone under the crosshair', () => {
    render(<CandleChart bars={BARS} market="stock" dateStyle={STYLE} daily onNearLeftEdge={() => undefined} />);
    expect(crosshairText()).toBe('3 Jun 2024');
  });

  it("writes a daily candle's own day, the market's, in the user's date format", () => {
    // 2024-06-03 00:00 UTC opens the crypto day of 3 June; in New York it is still 2 June.
    const cryptoDay = Date.UTC(2024, 5, 3) / 1000;
    const dated = (market: 'crypto' | 'stock', time: number, timeZone: string) => {
      render(
        <CandleChart
          bars={[{ ...BAR, time }]}
          market={market}
          dateStyle={{ timeZone, dateFormat: 'iso', timeFormat: '24' }}
          daily
          onNearLeftEdge={() => undefined}
        />,
      );
      const formatter = options.findLast((option) => option.localization?.timeFormatter)?.localization?.timeFormatter;
      cleanup();
      return formatter?.(time);
    };
    expect(dated('crypto', cryptoDay, 'America/New_York')).toBe('2024-06-03');
    expect(dated('crypto', cryptoDay, 'Asia/Tokyo')).toBe('2024-06-03');
    // A stock's day is its New York session, 22:30 in Tokyo is already the next day.
    const lateOpen = Date.UTC(2024, 5, 3, 13, 30) / 1000;
    expect(dated('stock', lateOpen, 'Asia/Tokyo')).toBe('2024-06-03');
  });

  it('stops scrolling before the first candle and 10 empty bars past the newest one', () => {
    render(<CandleChart bars={BARS} market="crypto" dateStyle={STYLE} daily onNearLeftEdge={() => undefined} />);
    expect(options[0]?.timeScale?.fixLeftEdge).toBe(true);
    const onRange = (range: { from: number; to: number }) => timeScale.onRange.forEach((handler) => handler(range));

    timeScale.size.position = 10;
    onRange({ from: 0, to: 10 });
    expect(timeScale.scrollToPosition).not.toHaveBeenCalled();

    timeScale.size.position = 25;
    onRange({ from: 0, to: 25 });
    expect(timeScale.scrollToPosition).toHaveBeenCalledWith(10, false);
  });

  it('offers the latest candle only when the view is back in history', () => {
    const DAY = 86_400;
    const days = Array.from({ length: 50 }, (_, i) => ({ ...BAR, time: i * DAY }));
    render(<CandleChart bars={days} market="crypto" dateStyle={STYLE} daily onNearLeftEdge={() => undefined} />);
    const onRange = (range: { from: number; to: number }) =>
      act(() => timeScale.onRange.forEach((handler) => handler(range)));
    const button = () => screen.queryByRole('button', { name: 'Go to the latest candle' });

    // All 50 candles fit: the library keeps the view 10 bars past the newest one (index 49).
    onRange({ from: 0, to: 59 });
    expect(button()).toBeNull();

    onRange({ from: 0, to: 40 });
    expect(button()).not.toBeNull();
  });

  it('keeps the zoom limits as wide as the chart', () => {
    const DAY = 86_400;
    const days = Array.from({ length: 290 }, (_, i) => ({ ...BAR, time: i * DAY }));
    timeScale.size.width = 1000;
    render(<CandleChart bars={days} market="crypto" dateStyle={STYLE} daily onNearLeftEdge={() => undefined} />);
    expect(options.findLast((option) => option.timeScale?.maxBarSpacing)?.timeScale).toEqual({
      minBarSpacing: 1000 / 300,
      maxBarSpacing: 50,
    });

    timeScale.size.width = 400;
    timeScale.onSize.forEach((handler) => handler());
    expect(options.findLast((option) => option.timeScale?.maxBarSpacing)?.timeScale).toEqual({
      minBarSpacing: 400 / 300,
      maxBarSpacing: 20,
    });
  });

  it('keeps the volume off the price axis and writes prices as the legend does', () => {
    render(<CandleChart bars={BARS} market="crypto" dateStyle={STYLE} daily onNearLeftEdge={() => undefined} />);
    const volume = series.find((added) => added.kind === 'Histogram');
    expect(volume?.options[0]).toMatchObject({ lastValueVisible: false, priceLineVisible: false });
    const candles = series.find((added) => added.kind === 'Candlestick');
    const format = candles?.options.findLast((option) => option.priceFormat)?.priceFormat;
    expect(format?.type).toBe('custom');
    // The latest close is 1.5, so 4 decimals, with the legend's thousands separators.
    expect(format?.formatter?.(68897.19)).toBe('68,897.1900');
  });

  it('opens on the newest 120 candles, as the mockup does', () => {
    const DAY = 86_400;
    const days = (count: number) =>
      Array.from({ length: count }, (_, i) => ({ time: i * DAY, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }));
    const chart = (bars: ReturnType<typeof days>) => (
      <CandleChart bars={bars} market="crypto" dateStyle={STYLE} daily onNearLeftEdge={() => undefined} />
    );

    const { rerender } = render(chart(days(500)));
    expect(timeScale.setVisibleLogicalRange.mock.calls).toEqual([[{ from: 379.5, to: 499.5 }]]);
    // New candles later leave the view where the user has it.
    rerender(chart(days(501)));
    expect(timeScale.setVisibleLogicalRange).toHaveBeenCalledTimes(1);
    cleanup();

    // Fewer fill the chart at the default spacing.
    render(chart(days(50)));
    expect(timeScale.setVisibleLogicalRange).toHaveBeenCalledTimes(1);
    cleanup();

    // A phone's chart, 270 px wide, opens on the 45 that fit at 6 px each.
    timeScale.setVisibleLogicalRange.mockClear();
    timeScale.size.width = 270;
    render(chart(days(500)));
    expect(timeScale.setVisibleLogicalRange.mock.calls).toEqual([[{ from: 454.5, to: 499.5 }]]);
  });
});
