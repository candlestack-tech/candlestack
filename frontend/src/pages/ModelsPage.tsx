import type { ReactNode } from 'react';

import { PageTitle } from '@/components/PageTitle';
import { formatUsage, ModelsGrid, UploadCard, UploadDialog, useModels } from '@/features/models';
import { plural } from '@/lib/plural';
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

// The user's uploaded models (#208, #272, mockups/app ddModels): the title with the Upload card
// beside it, then a tile per model, each opening the model's page.
export function ModelsPage() {
  usePageTitle('Models');
  const { data } = useModels();
  const description = useDescription();
  return (
    <>
      <PageTitle
        variant="workspace"
        title="Models"
        description={description}
        action={
          <UploadDialog>
            <UploadCard className="w-[300px] max-w-full flex-none" />
          </UploadDialog>
        }
      />
      {data && <ModelsGrid models={data.models} />}
    </>
  );
}
