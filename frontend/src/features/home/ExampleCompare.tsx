import { cn } from '@/lib/utils';

// The compare page in small on the home page (#359): sample figures written here and never
// fetched, as market data is for signed-in accounts only (#340). The lines are the return in
// percent over the period, and the table marks the best value of a row in green and the worst in
// red, as the page does.

type Run = {
  name: string;
  short: string;
  color: string;
  line: readonly number[];
  values: readonly number[];
};

const RUNS: readonly Run[] = [
  {
    name: 'LSTM · Threshold',
    short: 'LSTM',
    color: 'var(--compare-1)',
    line: [0, 4, 3, 9, 14, 11, 18, 24, 21, 29, 36, 33, 41, 48, 46, 55, 62, 60, 68, 74],
    values: [74, -11.2, 1.18, 54.1, 3.1],
  },
  {
    name: 'TSMixer · K-means',
    short: 'TSMixer',
    color: 'var(--compare-2)',
    line: [0, -3, 6, 2, 12, 22, 15, 8, 19, 33, 27, 44, 38, 26, 41, 58, 49, 72, 88, 96],
    values: [96, -24.6, 0.87, 50.3, 6.2],
  },
  {
    name: 'GRU · Hysteresis',
    short: 'GRU',
    color: 'var(--compare-3)',
    line: [0, 2, -2, -5, -1, -7, -10, -6, -12, -9, -15, -11, -18, -14, -20, -17, -13, -19, -16, -21],
    values: [-21, -29.4, -0.24, 46.2, 4.4],
  },
];

// The rows: their name, which way is better, and how a value reads.
const percent = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toFixed(1)} %`;
const plainPercent = (value: number) => `${value.toFixed(1)} %`;
const ROWS: readonly { label: string; better: 1 | -1; write: (value: number) => string }[] = [
  { label: 'Total return', better: 1, write: percent },
  // A fall is negative here too: the nearer to zero, the better, so higher is better.
  { label: 'Max drawdown', better: 1, write: percent },
  { label: 'Sharpe', better: 1, write: (value) => `${value < 0 ? '−' : ''}${Math.abs(value).toFixed(2)}` },
  { label: 'Win rate', better: 1, write: plainPercent },
  { label: 'Fees paid', better: -1, write: plainPercent },
];

const TOP = 100;
const BOTTOM = -50;
const TICKS = [100, 50, 0, -50];
const WIDTH = 1000;
const HEIGHT = 240;

const yOf = (value: number) => ((TOP - value) / (TOP - BOTTOM)) * HEIGHT;
const pathOf = (line: readonly number[]) =>
  line.map((value, index) => `${index === 0 ? 'M' : 'L'}${(index / (line.length - 1)) * WIDTH} ${yOf(value)}`).join('');

function Axis() {
  return (
    <div aria-hidden="true" className="relative h-[170px] w-[46px] flex-none">
      {TICKS.map((tick) => (
        <span
          key={tick}
          className="absolute right-0 -translate-y-1/2 font-mono text-[11px] whitespace-nowrap text-muted-foreground"
          style={{ top: `${(yOf(tick) / HEIGHT) * 100}%` }}
        >
          {percent(tick).replace('.0', '')}
        </span>
      ))}
    </div>
  );
}

function Chart() {
  return (
    <div className="flex gap-2 px-4">
      <div aria-hidden="true" className="relative h-[170px] min-w-0 flex-1">
        {TICKS.map((tick) => (
          <div
            key={tick}
            className={cn('absolute inset-x-0 h-px', tick === 0 ? 'bg-muted-foreground' : 'bg-border')}
            style={{ top: `${(yOf(tick) / HEIGHT) * 100}%` }}
          />
        ))}
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          className="absolute inset-0 size-full overflow-visible"
        >
          {RUNS.map(({ short, color, line }) => (
            <path
              key={short}
              d={pathOf(line)}
              fill="none"
              stroke={color}
              strokeWidth={2}
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
      </div>
      <Axis />
    </div>
  );
}

const CELL = 'border-t px-3 py-2 text-[13px] max-[480px]:px-2';
const MARKS = {
  best: 'bg-[color-mix(in_srgb,var(--up)_14%,transparent)] font-semibold text-up',
  worst: 'bg-[color-mix(in_srgb,var(--down)_14%,transparent)] font-semibold text-down',
};

function markOf(values: readonly number[], index: number, better: 1 | -1) {
  const value = values[index] ?? 0;
  const best = better > 0 ? Math.max(...values) : Math.min(...values);
  const worst = better > 0 ? Math.min(...values) : Math.max(...values);
  if (value === best) return MARKS.best;
  return value === worst ? MARKS.worst : undefined;
}

function Heads() {
  return (
    <thead>
      <tr>
        <th scope="col" aria-label="Metric" className={cn(CELL, 'w-[30%] font-normal')}>
          <span className="sr-only">Metric</span>
        </th>
        {RUNS.map(({ short, color }) => (
          <th key={short} scope="col" aria-label={short} className={cn(CELL, 'text-left font-semibold')}>
            <span className="flex items-center gap-1.5">
              <span aria-hidden="true" className="size-2 flex-none rounded-full" style={{ background: color }} />
              <span className="truncate">{short}</span>
            </span>
          </th>
        ))}
      </tr>
    </thead>
  );
}

function Table() {
  return (
    <table className="w-full table-fixed border-collapse">
      <Heads />
      <tbody>
        {ROWS.map(({ label, better, write }, row) => {
          const values = RUNS.map((run) => run.values[row] ?? 0);
          return (
            <tr key={label}>
              <th scope="row" className={cn(CELL, 'text-left font-normal text-muted-foreground')}>
                {label}
              </th>
              {values.map((value, index) => (
                <td
                  key={RUNS[index]?.short}
                  className={cn(
                    CELL,
                    'font-mono text-[12.5px] whitespace-nowrap max-[480px]:text-[11.5px]',
                    markOf(values, index, better),
                  )}
                >
                  {write(value)}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ExampleCompare() {
  return (
    <div className="overflow-hidden rounded-xl border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-3.5 pb-2">
        <span className="text-sm font-semibold">Equity</span>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {RUNS.map(({ name, color }) => (
            <span key={name} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden="true" className="h-[3px] w-3.5 rounded-sm" style={{ background: color }} />
              {name}
            </span>
          ))}
        </div>
      </div>
      <Chart />
      <div
        aria-hidden="true"
        className="flex justify-between px-4 pt-2 pr-[70px] pb-3 font-mono text-[11px] text-muted-foreground"
      >
        <span>Year 1</span>
        <span>Year 2</span>
        <span>Year 3</span>
      </div>
      <Table />
      <div className="flex items-center justify-between gap-3 border-t px-3 py-2 text-xs text-muted-foreground">
        <span className="rounded-full border px-2 py-0.5 font-mono text-[11px]/4 font-medium tracking-[0.06em]">
          EXAMPLE
        </span>
        <span>Sample figures, not market data</span>
      </div>
    </div>
  );
}
