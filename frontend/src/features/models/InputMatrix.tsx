import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';
import { useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';

import { catalogQueryOptions, type Manifest } from '@/api';
import { HINT } from '@/components/Hint';
import { cn } from '@/lib/utils';

import { columnsOf, groupsOf, rowsDrawn, shadesOf, type Column } from './manifest';

// Without an example there is nothing to shade by: every cell gets the same light tint.
const NO_VALUE = 18;

const repeat = (count: number) => ({ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` });

// A cell's tint, from its shade (--a): the accent over the surface; ink while its column is
// hovered; the accent at half strength and more while it is chosen, so the chosen column stands
// out in the accent rather than greyed out.
const CELL =
  'block h-[7px] rounded-[1.5px] bg-[color-mix(in_srgb,var(--accent)_var(--a),var(--surface))] last:h-[9px] group-hover:bg-[color-mix(in_srgb,var(--ink)_calc(var(--a)*0.8),var(--surface))] group-aria-pressed:bg-[color-mix(in_srgb,var(--accent)_calc(var(--a)*0.5_+_50%),var(--surface))]';

type GridProps = {
  columns: Column[];
  rows: number[];
  shades: number[][] | null;
  picked: number | null;
  onPick: (index: number) => void;
};

type ButtonProps = {
  name: string;
  index: number;
  rows: number[];
  shades: number[] | undefined;
  focused: boolean;
  picked: boolean;
  onFocus: () => void;
  onPick: () => void;
};

// A column (.p6-col): its cells top to bottom, outlined in the accent while it is chosen.
function ColumnButton({ name, index, rows, shades, focused, picked, onFocus, onPick }: ButtonProps) {
  return (
    <button
      type="button"
      tabIndex={focused ? 0 : -1}
      aria-pressed={picked}
      aria-label={`Column ${index + 1}, ${name}`}
      title={name}
      onFocus={onFocus}
      onClick={onPick}
      className="group flex cursor-pointer flex-col gap-px rounded-[3px] outline-offset-1 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-primary aria-pressed:outline-2 aria-pressed:outline-solid aria-pressed:outline-primary"
    >
      {rows.map((row, cell) => (
        <i key={row} className={CELL} style={{ '--a': `${shades?.[cell] ?? NO_VALUE}%` } as CSSProperties} />
      ))}
    </button>
  );
}

// The columns as buttons, one tab stop for all of them: the arrows, Home and End move between
// them, Enter or Space shows one (or hides it again).
function ColumnButtons({ columns, rows, shades, picked, onPick }: GridProps) {
  const [focus, setFocus] = useState(0);
  const grid = useRef<HTMLDivElement>(null);
  const move = (event: KeyboardEvent) => {
    const last = columns.length - 1;
    const next = { ArrowRight: focus + 1, ArrowLeft: focus - 1, Home: 0, End: last }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const index = Math.max(0, Math.min(last, next));
    setFocus(index);
    grid.current?.querySelectorAll('button')[index]?.focus();
  };
  return (
    <div
      ref={grid}
      role="toolbar"
      tabIndex={-1}
      aria-label="Input columns"
      onKeyDown={move}
      className="col-start-2 row-start-2 grid gap-0.5"
      style={repeat(columns.length)}
    >
      {columns.map((column, index) => (
        <ColumnButton
          key={column.name}
          name={column.name}
          index={index}
          rows={rows}
          shades={shades?.[index]}
          focused={index === focus}
          picked={picked === index}
          onFocus={() => setFocus(index)}
          onPick={() => onPick(index)}
        />
      ))}
    </div>
  );
}

// What the chosen column is (.p5-col): its place and name with the catalog's words, its
// settings and how it is scaled.
function ColumnFacts({ column, index }: { column: Column; index: number }) {
  const facts = [
    [`${index + 1} · ${column.name}`, column.description],
    ['Settings', column.settings],
    ['Scaling', column.scaling],
  ];
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-2 rounded-lg bg-muted px-3 py-2.5 text-[13px]">
      {facts.map(([term, value]) => (
        <div key={term} className="min-w-0">
          <dt className="font-mono text-[11.5px] break-words text-muted-foreground">{term}</dt>
          <dd className="mt-0.5 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// The groups over the columns (.p6-groups), each as wide as its columns.
function Groups({ columns }: { columns: Column[] }) {
  return (
    <div className="col-start-2 grid gap-0.5" style={repeat(columns.length)} aria-hidden="true">
      {groupsOf(columns).map(({ group, span }, index) => (
        <span
          key={`${group}-${index}`}
          title={group}
          className="truncate border-t-2 pt-[3px] font-mono text-[10.5px] text-muted-foreground"
          style={{ gridColumn: `span ${span}` }}
        >
          {group}
        </span>
      ))}
    </div>
  );
}

// The rows' axis (.p6-ax): the oldest at the top, how many, now at the bottom.
function RowAxis({ length }: { length: number }) {
  return (
    <div className="col-start-1 row-start-2 flex flex-col items-end justify-between font-mono text-[11px] whitespace-nowrap text-muted-foreground">
      <span>oldest</span>
      <span>{length} rows</span>
      <span className="text-foreground">now</span>
    </div>
  );
}

// What the model gives for it (.p6-out): one number.
function Prediction() {
  return (
    <div className="col-start-3 row-start-2 flex items-end gap-2 text-muted-foreground">
      <ArrowRight aria-hidden="true" className="mb-[26px] size-4" />
      <div className="flex flex-col items-center gap-0.5 text-xs">
        <span className="size-[22px] rounded-[4px] bg-primary" />
        <span className="font-mono text-foreground">[1]</span>
        <span className={HINT}>prediction</span>
      </div>
    </div>
  );
}

type Props = {
  manifest: Manifest;
  // The line under the matrix while no column is chosen; none when left out.
  hint?: string;
};

// The model's input as a picture (mockups/app, inputMatrix): rows down, the oldest at the top and
// the newest at the bottom, the columns across under their groups, then the one number it
// predicts. The shades are the manifest's example, when it has one. A column shows what it is
// on a click.
export function InputMatrix({ manifest, hint }: Props) {
  const { data: catalog } = useQuery(catalogQueryOptions());
  const [picked, setPicked] = useState<number | null>(null);
  const columns = columnsOf(manifest, catalog);
  const rows = rowsDrawn(manifest.input.window.length);
  const shades = shadesOf(manifest, rows);
  const chosen = picked === null ? undefined : columns[picked];
  return (
    <>
      <div className="overflow-x-auto p-0.5">
        <div
          className="grid gap-x-2.5 gap-y-1.5"
          style={{ gridTemplateColumns: `minmax(46px, auto) minmax(${columns.length * 12}px, 1fr) auto` }}
        >
          <Groups columns={columns} />
          <RowAxis length={manifest.input.window.length} />
          <ColumnButtons
            columns={columns}
            rows={rows}
            shades={shades}
            picked={picked}
            onPick={(index) => setPicked((current) => (current === index ? null : index))}
          />
          <Prediction />
          <div className="col-start-2 text-center font-mono text-[11px] text-muted-foreground">
            {columns.length} {columns.length === 1 ? 'column' : 'columns'}
          </div>
        </div>
      </div>
      <div aria-live="polite" className={cn(!chosen && !hint && 'empty:hidden')}>
        {chosen && picked !== null ? (
          <ColumnFacts column={chosen} index={picked} />
        ) : (
          hint && <p className={HINT}>{hint}</p>
        )}
      </div>
    </>
  );
}
