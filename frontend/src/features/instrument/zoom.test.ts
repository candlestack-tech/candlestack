import { describe, expect, it } from 'vitest';

import { openBars, zoomLimits } from './zoom';

describe('chart zoom', () => {
  it('stops zooming in at 20 candles and zooming out once all of them fit with 10 bars to spare', () => {
    expect(zoomLimits(1000, 490)).toEqual({ minBarSpacing: 2, maxBarSpacing: 50 });
  });

  it("never lets zooming out go under the library's 0.5 px a bar", () => {
    expect(zoomLimits(1000, 5000)).toEqual({ minBarSpacing: 0.5, maxBarSpacing: 50 });
  });

  it('lets a few candles fill the chart, with zooming in and out at the same spacing', () => {
    expect(zoomLimits(1000, 5)).toEqual({ minBarSpacing: 50, maxBarSpacing: 50 });
  });

  it('keeps the library defaults before the chart has a width', () => {
    expect(zoomLimits(0, 100)).toBeNull();
  });

  it('opens on 120 candles, or on as many as fit at 6 px on a narrow chart', () => {
    expect(openBars(1040)).toBe(120);
    expect(openBars(270)).toBe(45);
    expect(openBars(0)).toBe(120);
  });
});
