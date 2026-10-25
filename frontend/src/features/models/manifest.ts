import type { Catalog, Manifest, ManifestSummary, Model, ModelState } from '@/api';

// How the app writes what a model's manifest says (mockups/app: mdTileA, pageModel, the upload's
// second step) and how it draws the input as a matrix (inputMatrix).

// The states as the pill names them, and what its title says.
export const STATE_TEXT: Record<ModelState, string> = {
  verified: 'Verified',
  ready: 'Ready',
  needs_setup: 'Needs setup',
};
export const STATE_WHY: Record<ModelState, string> = {
  verified: 'Its manifest is checked and its example gives the expected output.',
  ready: 'Its manifest passed every check.',
  needs_setup: 'Add a manifest to tell CandleStack how to build its input.',
};

// The rows a representation makes, short on a tile ("Renko · 30m") and long in the upload's
// summary ("Renko bricks, 30m"). A block the app has no name for is written by its id.
const REPRESENTATIONS: Record<string, { short: string; long: string }> = {
  'candles@1': { short: 'Candles', long: 'Candles' },
  'renko-triples@1': { short: 'Renko', long: 'Renko bricks' },
  'kagi@1': { short: 'Kagi', long: 'Kagi lines' },
};

export function representationName(id: string, long = false): string {
  const names = REPRESENTATIONS[id];
  if (!names) return id;
  return long ? names.long : names.short;
}

// A shape as the API writes it: [25, 23].
export const formatDims = (shape: readonly number[]) => `[${shape.join(', ')}]`;

// Rows by columns, spaced: 25 × 23.
export const formatGrid = (shape: readonly number[]) => shape.join(' × ');

// The candles a backtest loads before its first sample: exact with every candle a row, an
// estimate (≈) with rows that span several candles.
export function formatWarmUp({ warm_up }: ManifestSummary): string {
  const candles = `${warm_up.candles.toLocaleString('en-US')} ${warm_up.candles === 1 ? 'candle' : 'candles'}`;
  return warm_up.kind === 'estimate' ? `≈ ${candles}` : candles;
}

// What it reads: "Renko · 30m".
export const formatReads = (summary: ManifestSummary) =>
  `${representationName(summary.representation.id)} · ${summary.timeframe}`;

// What it reads: "Renko · 30m · 25 × 23" from its manifest; without one the shape of the file
// and that it has none.
export function readsOf(model: Model): string {
  const summary = model.manifest_summary;
  if (summary) {
    return `${representationName(summary.representation.id)} · ${summary.timeframe} · ${formatGrid(summary.input_shape)}`;
  }
  return model.input_shape ? `in ${formatGrid(model.input_shape)} · no manifest` : 'no manifest';
}

// A block's parameters as the manifest gives them: "window 20, dev 2"; "none" without any.
export function formatParams(params: Record<string, unknown> | undefined): string {
  const entries = Object.entries(params ?? {});
  if (entries.length === 0) return 'none';
  return entries.map(([name, value]) => `${name.replaceAll('_', ' ')} ${String(value)}`).join(', ');
}

// One column of the input, with what the catalog says about its block.
export type Column = {
  name: string;
  // The catalog's display group (Candle, Bands, Momentum, ...).
  group: string;
  description: string;
  settings: string;
  scaling: string;
};

const NO_SCALING = 'none@1';

// How a column is scaled, by its normalization: "as is" without one, the BLESK models' by its
// name, any other in the catalog's words.
function scalingOf(id: string, catalog: Catalog | undefined): string {
  if (id === NO_SCALING) return 'as is';
  if (id.startsWith('blesk-norm@')) return 'BLESK scaling';
  return catalog?.blocks.find((block) => block.id === id)?.description ?? id;
}

// The columns in their order. Before the catalog arrives (or for a block it does not list) a
// column has no group or description of its own.
export function columnsOf(manifest: Manifest, catalog: Catalog | undefined): Column[] {
  return manifest.input.features.map((feature) => {
    const block = catalog?.blocks.find((item) => item.id === feature.id);
    return {
      name: feature.name,
      group: block?.group ?? 'Columns',
      description: block?.description ?? feature.id,
      settings: formatParams(feature.params),
      scaling: scalingOf(feature.norm?.id ?? NO_SCALING, catalog),
    };
  });
}

// The groups over the columns, each as wide as the columns next to each other that share it.
export function groupsOf(columns: readonly Column[]): { group: string; span: number }[] {
  const groups: { group: string; span: number }[] = [];
  for (const column of columns) {
    const last = groups.at(-1);
    if (last?.group === column.group) last.span += 1;
    else groups.push({ group: column.group, span: 1 });
  }
  return groups;
}

// At most this many rows are drawn: a window of 1000 rows would not fit the page. A longer window
// shows rows spread evenly over it, the oldest and the newest among them.
export const ROWS_DRAWN = 32;

export function rowsDrawn(length: number): number[] {
  if (length <= ROWS_DRAWN) return Array.from({ length }, (_, row) => row);
  return Array.from({ length: ROWS_DRAWN }, (_, index) => Math.round((index * (length - 1)) / (ROWS_DRAWN - 1)));
}

// The shade of each drawn cell from the first sample of the manifest's example, by column
// (column, then row), scaled to the column's lowest and highest value: 12 % to 74 % of the
// accent. Without an example there are no values to draw: null.
export function shadesOf(manifest: Manifest, rows: readonly number[]): number[][] | null {
  const sample = manifest.example?.input[0];
  if (!sample) return null;
  const width = manifest.input.features.length;
  return Array.from({ length: width }, (_, column) => {
    const values = rows.map((row) => sample[row]?.[column] ?? 0);
    const low = Math.min(...values);
    const span = Math.max(...values) - low;
    return values.map((value) => Math.round(12 + (span > 0 ? (value - low) / span : 0.5) * 62));
  });
}
