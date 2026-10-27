// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CandleChart } from './CandleChart';

// The chart draws on a canvas, which jsdom has not: a stand-in keeps what the chart was told.
const options = vi.hoisted(() => [] as { localization?: { timeFormatter?: (time: number) => string } }[]);
const timeScale = vi.hoisted(() => ({
  subscribeVisibleLogicalRangeChange: vi.fn(),
  getVisibleLogicalRange: () => null,
  setVisibleLogicalRange: vi.fn(),
  scrollToRealTime: vi.fn(),
}));

vi.mock('lightweight-charts', () => {
  const series = () => ({ applyOptions: vi.fn(), setData: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) });
  return {
    CandlestickSeries: 'Candlestick',
    HistogramSeries: 'Histogram',
    ColorType: { Solid: 'solid' },
    createChart: () => ({
      applyOptions: (next: (typeof options)[number]) => options.push(next),
      addSeries: series,
      timeScale: () => timeScale,
      subscribeCrosshairMove: vi.fn(),
      remove: vi.fn(),
    }),
  };
});

afterEach(() => {
  cleanup();
  options.length = 0;
  timeScale.setVisibleLogicalRange.mockClear();
});

// 2024-06-03 13:30 UTC, the NYSE open in summer time.
const SUMMER_OPEN = Date.UTC(2024, 5, 3, 13, 30) / 1000;
const BARS = [{ time: SUMMER_OPEN, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }];
const STYLE = { timeZone: 'UTC', dateFormat: 'dmy', timeFormat: '24' } as const;

// What the crosshair shows now: the last time formatter the chart was given.
function crosshairText(): string | undefined {
  return options.findLast((option) => option.localization?.timeFormatter)?.localization?.timeFormatter?.(SUMMER_OPEN);
}

describe('the candle chart', () => {
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
  });
});
