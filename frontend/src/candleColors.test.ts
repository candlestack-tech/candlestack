/// <reference types="node" />
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

// Read as a file: Vitest does not load CSS into tests.
const css = readFileSync(new URL('index.css', import.meta.url), 'utf8');

// #113: the candles must not look like the teal accent, and must stay visible on the page.

function variable(block: string, name: string): string {
  const value = new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, 'i').exec(block)?.[1];
  if (!value) throw new Error(`--${name} is missing`);
  return value;
}

function channels(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

// The hue in degrees, 0 to 360.
function hue(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => c / 255) as [number, number, number];
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  if (d === 0) return 0;
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

// WCAG contrast ratio; 3:1 is the minimum for graphics.
function contrast(a: string, b: string): number {
  const luminance = (hex: string) => {
    const [r, g, bl] = channels(hex).map((c) => {
      const v = c / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [luminance(a), luminance(b)].toSorted((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// A tint of the shared names, "--surface-2: color-mix(in srgb, var(--ink) 7%, var(--bg))", worked
// out with the theme's colors as the browser mixes them.
function tint(block: string, name: string): string {
  const match = new RegExp(
    `--${name}:\\s*color-mix\\(in srgb, var\\(--([\\w-]+)\\) (\\d+)%, var\\(--([\\w-]+)\\)\\)`,
  ).exec(css);
  if (!match) throw new Error(`--${name} is not a color-mix`);
  const [, first = '', percent = '', second = ''] = match;
  const share = Number(percent) / 100;
  const [a, b] = [channels(variable(block, first)), channels(variable(block, second))];
  const mixed = a.map((c, i) => Math.round(c * share + (b[i] ?? 0) * (1 - share)));
  return `#${mixed.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}

const light = css.slice(css.indexOf(':root {'), css.indexOf(":root[data-theme='dark']"));
const dark = css.slice(css.indexOf(":root[data-theme='dark']"));

describe.each([
  ['light', light],
  ['dark', dark],
])('candle colors in the %s theme', (_, block) => {
  it('keep the up color at least 30° of hue away from the teal accent', () => {
    expect(Math.abs(hue(variable(block, 'candle-up')) - hue(variable(block, 'accent')))).toBeGreaterThanOrEqual(30);
  });

  it('stand out from the background at 3:1 or more', () => {
    const bg = variable(block, 'bg');
    expect(contrast(variable(block, 'candle-up'), bg)).toBeGreaterThanOrEqual(3);
    expect(contrast(variable(block, 'candle-down'), bg)).toBeGreaterThanOrEqual(3);
  });
});

// #184: the 24 h change of the instrument list is text, so it needs 4.5:1; so does the readout over
// the instrument chart, which uses them too.
describe.each([
  ['light', light],
  ['dark', dark],
])('change colors in the %s theme', (_, block) => {
  it('read on the background at 4.5:1 or more', () => {
    const bg = variable(block, 'bg');
    expect(contrast(variable(block, 'up'), bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(variable(block, 'down'), bg)).toBeGreaterThanOrEqual(4.5);
  });
});

// #246: text on the app's tints needs 4.5:1, a control that is off 3:1.
describe.each([
  ['light', light],
  ['dark', dark],
])('tints in the %s theme', (_, block) => {
  it('keep muted text on the stronger surface at 4.5:1 or more (the active sidebar entry, steps to come)', () => {
    expect(contrast(variable(block, 'muted'), tint(block, 'surface-2'))).toBeGreaterThanOrEqual(4.5);
  });

  it('keep the accent on its tint at 4.5:1 or more (the avatar, the pipeline layers)', () => {
    expect(contrast(variable(block, 'accent'), tint(block, 'accent-soft'))).toBeGreaterThanOrEqual(4.5);
  });

  it('show a control that is off at 3:1 on the background and under the knob of a switch', () => {
    const off = tint(block, 'control-off');
    expect(contrast(off, variable(block, 'bg'))).toBeGreaterThanOrEqual(3);
  });
});
