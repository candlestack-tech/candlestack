import type { ReactNode } from 'react';

import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { HINT } from '@/components/Hint';
import { UploadIcon } from '@/components/icons';
import { PageTitle } from '@/components/PageTitle';
import { Button } from '@/components/ui/button';
import { formatUsage, ModelsGrid, UploadDialog, useModels } from '@/features/models';
import { plural } from '@/lib/plural';
import { cn } from '@/lib/utils';
import { usePageTitle } from '@/lib/usePageTitle';

// What the line under the title says: how many models, the storage they take and how many still
// need a manifest; why they could not load; an empty line while they load.
function useDescription(): ReactNode {
  const { data, error } = useModels();
  if (data) {
    const setup = data.models.filter((model) => model.state === 'needs_setup').length;
    return (
      <>
        {`${plural(data.models.length, 'model')} · ${formatUsage(data.usage.used_bytes, data.usage.quota_bytes)} used`}
        {setup > 0 && (
          <>
            {' · '}
            <span className="text-warn">
              {setup} {setup === 1 ? 'needs' : 'need'} a manifest
            </span>
          </>
        )}
      </>
    );
  }
  // A no-break space while they load keeps the line, so the title row does not move.
  return error?.message ?? ' ';
}

// Upload model beside the title (#220): an outline button with the upload icon and what it takes
// under it, which opens the upload dialog. Not the accent: New backtest in the app header is the
// one primary button.
function UploadModel() {
  return (
    <div className="flex flex-col items-start gap-1 sm:items-end">
      <UploadDialog>
        <Button type="button" variant="outline" className={cn(BUTTON_SIZES.default, OUTLINE_BUTTON)}>
          <UploadIcon aria-hidden="true" />
          Upload model
        </Button>
      </UploadDialog>
      <span className={HINT}>Keras or ONNX, up to 50 MB</span>
    </div>
  );
}

// The user's uploaded models (#208, #272, mockups/app ddModels): the title with Upload model at
// the right (under it when the row is too narrow), then a tile per model across the whole width,
// each opening the model's page.
export function ModelsPage() {
  usePageTitle('Models');
  const { data } = useModels();
  const description = useDescription();
  return (
    <>
      <PageTitle variant="workspace" title="Models" description={description} action={<UploadModel />} />
      {data && <ModelsGrid models={data.models} />}
    </>
  );
}
