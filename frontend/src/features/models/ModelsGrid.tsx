import { useState } from 'react';

import type { Model } from '@/api';

import { ModelTile } from './ModelTile';
import { ManifestDialog } from './UploadDialog';
import { useStarModel } from './useModels';

// The tiles in the API's order (oldest first), 208 px at least, as many as fit in a row, and the
// Add manifest dialog they open. No models, no tiles: the title row says how many.
export function ModelsGrid({ models }: { models: Model[] }) {
  // The dialog keeps its model while it fades out.
  const [dialog, setDialog] = useState<{ model: Model | null; open: boolean }>({ model: null, open: false });
  const star = useStarModel();
  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(208px,1fr))] gap-4">
        {models.map((model) => (
          <ModelTile
            key={model.id}
            model={model}
            onStar={(item) => star.mutate(item)}
            onAddManifest={(item) => setDialog({ model: item, open: true })}
          />
        ))}
      </div>
      <ManifestDialog
        model={dialog.model}
        open={dialog.open}
        onOpenChange={(open) => setDialog((current) => ({ ...current, open }))}
      />
    </>
  );
}
