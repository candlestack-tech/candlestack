import { Link } from '@tanstack/react-router';

import type { Model } from '@/api';
import { ErrorNote } from '@/components/ErrorNote';
import { Hint } from '@/components/Hint';
import { ModelsIcon } from '@/components/icons';
import { ROW_OPENER, RowStar } from '@/features/backtests';
import { formatShape, FrameworkTile, frameworkOf } from '@/features/models';
import { plural } from '@/lib/plural';
import { formatSharpe } from '@/lib/results';
import { cn } from '@/lib/utils';

import { starredModels } from './order';
import { MoreRow, STARRED_ROW, TOP, Widget } from './widgets';

// The framework's tile, the name over what it reads and its framework ("in 25×23 → 1 · Keras"),
// and at the right its best Sharpe ratio over how many backtests used it. The row opens the
// model's page.
function Row({ model, onStar }: { model: Model; onStar: (model: Model) => void }) {
  const framework = frameworkOf(model.framework);
  const name = framework ?? model.framework;
  const shape = formatShape(model.input_shape, model.output_shape);
  return (
    <div className={cn(STARRED_ROW, 'grid-cols-[auto_auto_minmax(0,1fr)_auto]')}>
      <RowStar starred label={`Unstar model ${model.name}`} onToggle={() => onStar(model)} />
      {framework && <FrameworkTile framework={framework} />}
      <span className={cn('flex min-w-0 flex-col', !framework && 'col-span-2')}>
        <Link
          to="/models/$modelId"
          params={{ modelId: model.id }}
          className={cn(ROW_OPENER, 'truncate text-[13px] font-medium')}
        >
          {model.name}
        </Link>
        <span className="truncate font-mono text-[11.5px]/[normal] text-muted-foreground">
          {shape === null ? name : `in ${shape} · ${name}`}
        </span>
      </span>
      <span className="flex flex-col items-end font-mono text-xs/[normal] whitespace-nowrap">
        <b className="text-sm/[normal] font-semibold">{formatSharpe(model.best_sharpe)}</b>
        <span className="text-[11px]/[normal] text-muted-foreground">{plural(model.backtest_count, 'backtest')}</span>
      </span>
    </div>
  );
}

type Props = { models: readonly Model[] | undefined; error: Error | null; onStar: (model: Model) => void };

// The rows, why the models could not load, or how to get a model here.
function Body({ models, error, onStar }: Props) {
  if (!models) return error && <ErrorNote>{error.message}</ErrorNote>;
  const starred = starredModels(models);
  if (starred.length === 0) return <Hint>Star a model to keep it here.</Hint>;
  return (
    <>
      <div className="flex flex-col">
        {starred.slice(0, TOP).map((model) => (
          <Row key={model.id} model={model} onStar={onStar} />
        ))}
      </div>
      <MoreRow total={starred.length} to="/models" />
    </>
  );
}

// The four best starred models, then More to the Models page.
export function StarredModels(props: Props) {
  return (
    <Widget icon={ModelsIcon} title="Starred models" aside={<Hint>by best Sharpe</Hint>}>
      <Body {...props} />
    </Widget>
  );
}
