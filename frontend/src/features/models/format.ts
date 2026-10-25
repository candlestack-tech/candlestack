import { formatPriceWith } from '@/lib/numbers';

// How the Models part writes sizes, parameters and shapes (mockups/app, the Models page and the
// upload dialog). Megabytes are binary, as the API's limits: 1 MB = 1,048,576 bytes.

const MB = 1024 * 1024;

// A file that is not empty never reads 0.0 MB: it takes some of the storage.
const megabytes = (bytes: number) => (bytes > 0 ? Math.max(bytes / MB, 0.1) : 0);

// A file's size with one decimal: 14.2 MB, 3.0 MB.
export function formatSize(bytes: number): string {
  return `${formatPriceWith(megabytes(bytes), 1)} MB`;
}

// Megabytes without a trailing zero, as the mockup writes the storage: 0, 29.5, 100.
function formatMegabytes(mb: number): string {
  return formatPriceWith(mb, Number.isInteger(Math.round(mb * 10) / 10) ? 0 : 1);
}

// `{used} MB of {quota} MB`, the storage the user's models take. Storage that is not full never
// reads as full: what would round up to the quota is rounded down instead (99.9 MB of 100 MB).
export function formatUsage(used: number, quota: number): string {
  const quotaMb = quota / MB;
  let usedMb = Math.round(megabytes(used) * 10) / 10;
  if (used < quota && usedMb >= quotaMb) usedMb = Math.floor((used / MB) * 10) / 10;
  return `${formatMegabytes(usedMb)} MB of ${formatMegabytes(quotaMb)} MB`;
}

// The shape a model reads and gives: "25×23 → 1" in the dashboard's starred models, "25 × 23 → 1"
// spaced. Without an input shape there is nothing to say (null); without an output, the input.
export function formatShape(input: number[] | null, output: number[] | null, spaced = false): string | null {
  if (input === null || input.length === 0) return null;
  const times = spaced ? ' × ' : '×';
  const text = input.join(times);
  return output === null || output.length === 0 ? text : `${text} → ${output.join(times)}`;
}

// "lstm.keras" as its stem "lstm" and the extension ".keras", which a rename keeps.
export function splitExtension(name: string): [string, string] {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ''];
}
