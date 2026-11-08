import { s } from './dom';
import { formatDay } from './format';

// The widget's charts, as SVG: lines over real time (UTC epoch seconds), a dashed line at the
// start value, and the first and last day under them. A null value is a gap (a backtest of a
// comparison outside its own period). A chart is drawn at the card's width in pixels (`width`),
// so its text keeps its size in a narrow chat; the widget draws it again when the width changes.

const SIDE = 8;
const TOP = 10;
const AXIS = 16;

export type Line = { values: readonly (number | null)[]; className: string; label?: string };

type Size = [width: number, height: number];

type Scale = { x: (time: number) => number; y: (value: number) => number };

function extent(values: Iterable<number>): [number, number] {
  let low = Number.POSITIVE_INFINITY;
  let high = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    low = Math.min(low, value);
    high = Math.max(high, value);
  }
  return [low, high];
}

function scale(times: readonly number[], values: number[], [width, height]: Size): Scale {
  const [first, last] = extent(times);
  const [low, high] = extent(values);
  const room = (high - low) * 0.06 || Math.abs(high) * 0.06 || 0.01;
  const bottom = height - AXIS;
  return {
    x: (time) => SIDE + ((time - first) / (last - first || 1)) * (width - 2 * SIDE),
    y: (value) => TOP + (1 - (value - (low - room)) / (high - low + 2 * room)) * (bottom - TOP),
  };
}

const point = (x: number, y: number) => `${x.toFixed(1)},${y.toFixed(1)}`;

// The path of a line, broken where it has no value.
export function linePath(times: readonly number[], values: readonly (number | null)[], { x, y }: Scale): string {
  let path = '';
  let drawing = false;
  values.forEach((value, index) => {
    const time = times[index];
    if (value === null || time === undefined || !Number.isFinite(value)) {
      drawing = false;
      return;
    }
    path += `${drawing ? 'L' : 'M'}${point(x(time), y(value))}`;
    drawing = true;
  });
  return path;
}

function axis(times: readonly number[], [width, height]: Size): SVGElement[] {
  const [first, last] = extent(times);
  const y = height - 3;
  return [
    s('text', { x: SIDE, y, class: 'axis' }, formatDay(first)),
    s('text', { x: width - SIDE, y, class: 'axis', 'text-anchor': 'end' }, formatDay(last)),
  ];
}

const frame = ([width, height]: Size, label: string, className: string) => ({
  width,
  height,
  viewBox: `0 0 ${width} ${height}`,
  role: 'img',
  'aria-label': label,
  class: className,
});

type LineChart = { times: readonly number[]; lines: Line[]; base: number; baseLabel: string; label: string };

// Lines over time with the dashed start value (1.00 of the capital, or 0 %).
export function lineChart({ times, lines, base, baseLabel, label }: LineChart, size: Size): SVGElement {
  const values = lines.flatMap((line) => line.values.filter((value): value is number => value !== null));
  const at = scale(times, [...values, base], size);
  const baseY = at.y(base);
  const right = size[0] - SIDE;
  return s(
    'svg',
    frame(size, label, 'chart'),
    s('line', { x1: SIDE, x2: right, y1: baseY, y2: baseY, class: 'base' }),
    s('text', { x: right, y: baseY - 5, class: 'axis', 'text-anchor': 'end' }, baseLabel),
    ...lines.map((line) => s('path', { d: linePath(times, line.values, at), class: `line ${line.className}` })),
    ...axis(times, size),
  );
}

// The fall from the highest equity as an area under zero.
export function drawdownChart(
  times: readonly number[],
  drawdown: readonly number[],
  label: string,
  width: number,
): SVGElement {
  const size: Size = [width, 56];
  const deepest = Math.min(...drawdown, -0.0001);
  const y = (value: number) => 3 + (value / deepest) * (size[1] - 6);
  const at = { x: scale(times, [0], size).x, y };
  const first = times[0] ?? 0;
  const last = times.at(-1) ?? 0;
  const area = `M${point(at.x(first), y(0))}${linePath(times, drawdown, at).replace('M', 'L')}L${point(at.x(last), y(0))}Z`;
  return s('svg', frame(size, label, 'chart drawdown'), s('path', { d: area, class: 'fall' }));
}
