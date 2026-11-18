// How far the candle chart lets the user scroll and zoom, and how many candles it opens on.

// The chart opens on this many of the newest candles, as the mockup does (about four months of
// daily candles); fewer fill it at the default spacing from the right. A narrow chart opens on as
// many as fit at OPEN_SPACING pixels each, so a phone shows candles, not hairlines.
const OPEN_BARS = 120;
const OPEN_SPACING = 6;
// Empty bars the chart may show past the newest candle; scrolling further right stops there.
export const RIGHT_ROOM = 10;
// Zooming in stops when this many candles fill the chart.
const FEWEST_BARS = 20;

// The zoom limits for `count` bars on a time scale `width` pixels wide: zooming in stops at
// FEWEST_BARS, zooming out once all of them fit with RIGHT_ROOM to spare (no lower than the
// library's 0.5 px). Without a width yet (before the first layout), the library's defaults stay.
export function zoomLimits(width: number, count: number): { minBarSpacing: number; maxBarSpacing: number } | null {
  if (width <= 0) return null;
  const maxBarSpacing = width / FEWEST_BARS;
  return { minBarSpacing: Math.min(maxBarSpacing, Math.max(0.5, width / (count + RIGHT_ROOM))), maxBarSpacing };
}

// How many of the newest candles the chart opens on: OPEN_BARS, or fewer on a narrow chart.
export function openBars(width: number): number {
  return width > 0 ? Math.min(OPEN_BARS, Math.floor(width / OPEN_SPACING)) : OPEN_BARS;
}
