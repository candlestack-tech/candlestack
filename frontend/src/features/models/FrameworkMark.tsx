import type { CSSProperties } from 'react';

import { cn } from '@/lib/utils';

import { FRAMEWORK_MARKS, type Framework } from './frameworks';

// The brand colour, a quarter lighter in the dark theme so the dark marks (Keras, ONNX) stay
// visible on the dark background.
const FILL = 'fill-(--fw) dark:fill-[color-mix(in_srgb,var(--fw),#fff_25%)]';

const fw = (framework: Framework) => ({ '--fw': FRAMEWORK_MARKS[framework].color }) as CSSProperties;

function Path({ framework }: { framework: Framework }) {
  return <path d={FRAMEWORK_MARKS[framework].path} />;
}

// The mark on a square tinted with its colour (fwIcon): 28 px in lists and the upload dialog,
// 36 px on a model's tile, 40 px in the header of its page.
const TILE_SIZES = {
  default: { tile: 'size-7 rounded-[7px]', mark: 'size-[15px]' },
  lg: { tile: 'size-9 rounded-[9px]', mark: 'size-[19px]' },
  xl: { tile: 'size-10 rounded-[10px]', mark: 'size-5' },
};

type TileProps = { framework: Framework; size?: keyof typeof TILE_SIZES };

export function FrameworkTile({ framework, size = 'default' }: TileProps) {
  const sizes = TILE_SIZES[size];
  return (
    <span
      title={framework}
      style={fw(framework)}
      className={cn('grid flex-none place-items-center bg-[color-mix(in_srgb,var(--fw)_12%,transparent)]', sizes.tile)}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" className={cn(sizes.mark, FILL)}>
        <Path framework={framework} />
      </svg>
    </span>
  );
}

// The 14 px mark (fwMini), named for screen readers unless its name is written beside it.
export function FrameworkMini({ framework, named = true }: { framework: Framework; named?: boolean }) {
  const a11y = named ? { role: 'img', 'aria-label': framework } : { 'aria-hidden': true };
  return (
    <svg viewBox="0 0 24 24" {...a11y} style={fw(framework)} className={cn('size-3.5 flex-none', FILL)}>
      <Path framework={framework} />
    </svg>
  );
}
