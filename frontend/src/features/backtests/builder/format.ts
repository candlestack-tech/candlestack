import type { ManifestSummary } from '@/api';
import { manifestRepresentation } from '@/features/models';
import { plural } from '@/lib/plural';

import type { BuilderForm, DateWriter } from './form';

// How the builder writes what it chose (mockups/app, pageBuilder and its Run step).

// The rows the model reads, with the brick of Renko: "Renko 0.6 × ATR".
function rowsOf({ representation }: ManifestSummary): string {
  const name = manifestRepresentation(representation.id);
  const multiple = representation.params['atr_multiple'];
  return typeof multiple === 'number' ? `${name} ${multiple} × ATR` : name;
}

// The model's preprocessing in one line, from its manifest: "Renko 0.6 × ATR · 30m · 23 features
// · window 25".
export function preprocessingLine(summary: ManifestSummary): string {
  const shape = summary.input_shape;
  const parts = [rowsOf(summary), summary.timeframe];
  if (shape.length >= 2) parts.push(plural(shape.at(-1) ?? 0, 'feature'), `window ${shape[0]}`);
  return parts.join(' · ');
}

// The period as a range of days: "2 Jan 2025 – 31 Dec 2025".
export const periodLine = (form: Pick<BuilderForm, 'from' | 'to'>, write: DateWriter) =>
  `${write(form.from)} – ${write(form.to)}`;

const fixed = (value: number) => value.toFixed(2);

// What Advanced turned on, in the order of its rows.
const advancedParts = (form: BuilderForm) => [
  form.calibration === 'zscore' && 'z-score',
  form.calibration === 'rank' && 'rank',
  form.ema > 1 && `EMA ${form.ema}`,
  form.confirm > 1 && `confirm ${form.confirm}`,
  form.minHold > 1 && `hold ${form.minHold}`,
  form.positionSize < 100 && `size ${form.positionSize} %`,
  form.drawdownStop > 0 && `drawdown stop ${form.drawdownStop} %`,
];

// The decision in one line: the rule, the direction and the stop, then what Advanced turned on.
export function decisionLine(form: BuilderForm): string {
  const rule =
    form.rule === 'kmeans'
      ? `k-means, ${form.k} groups`
      : form.rule === 'hysteresis'
        ? `in ${fixed(form.threshold)}, out ${fixed(Math.min(form.exit, form.threshold))}`
        : `threshold ${fixed(form.threshold)}`;
  return [
    rule,
    form.direction === 'long' ? 'long only' : 'long and short',
    `stop ${form.stopLoss} %`,
    ...advancedParts(form),
  ]
    .filter(Boolean)
    .join(' · ');
}

export const fillLine = (honest: boolean) => (honest ? "Next candle's open" : 'Same close');

export const costsLine = (form: BuilderForm) =>
  `fees ${form.fees.trim()} % · slippage ${form.slippage.trim()} % · vs buy and hold`;
