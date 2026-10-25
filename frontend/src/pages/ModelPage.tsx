import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { useState } from 'react';

import { errorMessage, type Model } from '@/api';
import { ACCENT_BUTTON, BUTTON_SIZES } from '@/components/buttonStyles';
import { PageTitle } from '@/components/PageTitle';
import { Button } from '@/components/ui/button';
import { ModelBacktests } from '@/features/backtests';
import {
  ManifestDialog,
  ModelActions,
  ModelChecks,
  ModelFacts,
  ModelHeader,
  ModelInput,
  PageBox,
  useManifest,
  useModels,
} from '@/features/models';
import { cn } from '@/lib/utils';
import { usePageTitle } from '@/lib/usePageTitle';

const BACK =
  'mb-3.5 inline-flex items-center gap-1.5 text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground focus-visible:underline';

function BackToModels() {
  return (
    <Link to="/models" className={BACK}>
      ← Models
    </Link>
  );
}

// A model of the user that is gone (deleted in another tab, say) or never was; or, with the
// title Model, why the models could not load.
function NoModel({ title, message }: { title: string; message: string }) {
  usePageTitle(title);
  return (
    <>
      <BackToModels />
      <PageTitle variant="workspace" title={title} description={message} />
    </>
  );
}

// Why the models could not load. Once they did, the list stays when asking for it again fails
// (after a rename, say), so the page does too.
const ListError = ({ error }: { error: Error }) => (
  <NoModel title="Model" message={errorMessage(error, 'Could not load the model.')} />
);

type InputProps = {
  model: Model;
  manifest: ReturnType<typeof useManifest>;
  onAddManifest: () => void;
};

// The input drawn as a matrix; Add manifest in its place while the model has none.
function InputBox({ model, manifest, onAddManifest }: InputProps) {
  if (model.state === 'needs_setup') {
    return (
      <PageBox title="No manifest" className="items-start">
        <Button type="button" className={cn(BUTTON_SIZES.default, ACCENT_BUTTON)} onClick={onAddManifest}>
          Add manifest
        </Button>
      </PageBox>
    );
  }
  if (manifest.error) {
    return (
      <PageBox title="Input">
        <p className="text-[12.5px] text-down" role="alert">
          {manifest.error.message}
        </p>
      </PageBox>
    );
  }
  return <ModelInput manifest={manifest.data} />;
}

// A model's page (#272, mockups/app pageModel), one screen without tabs: the header with the
// state and the buttons, the facts, the input drawn as a matrix beside the checks, then the
// backtests that used it. The model comes from the list the Models page reads, its whole manifest
// from its own request. Backtest with it opens the builder with the model chosen.
export function ModelPage() {
  const { modelId } = useParams({ from: '/_app/models/$modelId' });
  const navigate = useNavigate();
  const models = useModels();
  const model = models.data?.models.find((item) => item.id === modelId);
  const manifest = useManifest(model);
  const [adding, setAdding] = useState(false);
  usePageTitle(model?.name ?? 'Model');

  if (!models.data) return models.error ? <ListError error={models.error} /> : <BackToModels />;
  if (!model) return <NoModel title="Not found" message="You have no model with this ID." />;

  const addManifest = () => setAdding(true);
  return (
    <>
      <BackToModels />
      <ModelHeader
        model={model}
        actions={
          <ModelActions
            model={model}
            manifest={manifest.data}
            onBacktest={() =>
              void navigate({ to: '/backtests/new', search: { model: model.id }, state: { from: 'models' } })
            }
            onAddManifest={addManifest}
            onDeleted={() => void navigate({ to: '/models' })}
          />
        }
      />
      <ModelFacts model={model} />
      <div className="mb-4 grid grid-cols-[minmax(0,2.2fr)_minmax(220px,1fr)] items-start gap-4 max-[860px]:grid-cols-[minmax(0,1fr)]">
        <InputBox model={model} manifest={manifest} onAddManifest={addManifest} />
        <ModelChecks model={model} manifest={manifest.data} />
      </div>
      <ModelBacktests name={model.name} />
      <ManifestDialog model={model} open={adding} onOpenChange={setAdding} />
    </>
  );
}
