import { Link } from '@tanstack/react-router';

import type { Model } from '@/api';
import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { HINT } from '@/components/Hint';
import { StarButton } from '@/components/StarButton';
import { Button } from '@/components/ui/button';
import { useDateStyle } from '@/features/preferences';
import { formatRecentDay } from '@/lib/dates';
import { pluralNoun } from '@/lib/plural';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { formatSize } from './format';
import { FrameworkTile } from './FrameworkMark';
import { API_FRAMEWORKS } from './frameworks';
import { readsOf } from './manifest';
import { StatePill } from './StatePill';

type Props = {
  model: Model;
  onStar: (model: Model) => void;
  onAddManifest: (model: Model) => void;
};

// The best Sharpe of its backtests and how many used it, side by side under a line.
function Stats({ model }: { model: Model }) {
  return (
    <div className="mt-auto grid grid-cols-2 gap-2 border-t pt-2.5">
      <div className="flex flex-col">
        <b className="font-mono text-[15px]/[normal] font-semibold">{formatSharpe(model.best_sharpe)}</b>
        <span className={HINT}>best Sharpe</span>
      </div>
      <div className="flex flex-col">
        <b className="font-mono text-[15px]/[normal] font-semibold">{model.backtest_count}</b>
        <span className={HINT}>{pluralNoun(model.backtest_count, 'backtest')}</span>
      </div>
    </div>
  );
}

// Add manifest while it has none, above the tile's link.
function AddManifest({ model, onAddManifest }: Pick<Props, 'model' | 'onAddManifest'>) {
  return (
    <div className="mt-auto">
      <Button
        type="button"
        variant="outline"
        className={cn(BUTTON_SIZES.sm, OUTLINE_BUTTON, 'relative z-10')}
        aria-label={`Add manifest to ${model.name}`}
        onClick={() => onAddManifest(model)}
      >
        Add manifest
      </Button>
    </div>
  );
}

// The framework's mark at the left; the state and the star at the right, the star above the link.
function TileTop({ model, onStar }: Pick<Props, 'model' | 'onStar'>) {
  return (
    <div className="flex items-start justify-between gap-2">
      <FrameworkTile framework={API_FRAMEWORKS[model.framework]} size="lg" />
      <span className="flex items-center gap-1">
        <StatePill state={model.state} />
        <StarButton
          starred={model.starred}
          label={`Star model ${model.name}`}
          onToggle={() => onStar(model)}
          className="relative z-10 m-0 -mt-1 -mr-1.5 text-[21px]"
        />
      </span>
    </div>
  );
}

// A model on the Models page (mockups/app, mdTileA): a square card with the framework's mark, its
// state and the star, the name, what it is and what it reads, then its results, or Add manifest
// while it has none. The whole tile opens the model's page: the name's link is stretched over it,
// and the star and Add manifest sit above it.
export function ModelTile({ model, onStar, onAddManifest }: Props) {
  const dateStyle = useDateStyle();
  return (
    <article className="relative flex aspect-square min-w-0 flex-col gap-2.5 rounded-lg border bg-card p-4 transition-[border-color,box-shadow] duration-150 has-[a:focus-visible]:ring-2 has-[a:focus-visible]:ring-ring/60 hover:border-[color-mix(in_srgb,var(--accent)_40%,var(--rule))] hover:shadow-[0_4px_14px_var(--shadow)] motion-reduce:transition-none">
      <TileTop model={model} onStar={onStar} />
      <div className="flex min-w-0 flex-col gap-0.5">
        <Link
          to="/models/$modelId"
          params={{ modelId: model.id }}
          title={model.name}
          className="truncate font-semibold outline-none after:absolute after:inset-0 after:rounded-lg"
        >
          {model.name}
        </Link>
        <span className={HINT}>
          {API_FRAMEWORKS[model.framework]} · {formatSize(model.size_bytes)} · added{' '}
          {formatRecentDay(new Date(model.created_at), dateStyle)}
        </span>
      </div>
      <div className="font-mono text-xs/[17px] text-muted-foreground">{readsOf(model)}</div>
      {model.state === 'needs_setup' ? (
        <AddManifest model={model} onAddManifest={onAddManifest} />
      ) : (
        <Stats model={model} />
      )}
    </article>
  );
}
