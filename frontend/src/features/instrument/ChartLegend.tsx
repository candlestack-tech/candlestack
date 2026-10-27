import type { Legend } from './legend';

function Value({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <span>
      <span className="text-muted-foreground">{label}</span> <span className={tone}>{value}</span>
    </span>
  );
}

// "O 334.20 H 335.10 L 333.80 C 334.90 +0.21% Vol 1.2K" over the top left of the chart, for the
// candle under the cursor, in the text shades of the candle colors (4.5:1). It lets the cursor
// through to the chart.
export function ChartLegend({ legend }: { legend: Legend }) {
  const tone = legend.up ? 'text-up' : 'text-down';
  return (
    <div className="pointer-events-none absolute top-1 left-1 z-10 flex max-w-[calc(100%-5rem)] flex-wrap gap-x-3 rounded bg-background/70 px-1.5 py-0.5 font-mono text-xs">
      <Value label="O" value={legend.open} tone={tone} />
      <Value label="H" value={legend.high} tone={tone} />
      <Value label="L" value={legend.low} tone={tone} />
      <Value label="C" value={legend.close} tone={tone} />
      <span className={tone}>{legend.change}</span>
      <Value label="Vol" value={legend.volume} tone={tone} />
    </div>
  );
}
