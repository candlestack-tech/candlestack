import { Eyebrow } from '@/components/Eyebrow';

import { formatUsage } from './format';
import { StorageMeter } from './StorageMeter';
import { useModels } from './useModels';

// The storage the models take, at the sidebar's foot (mockups/app, .dd-sfoot): a 6 px meter and
// the megabytes under it, on one line ("models" is in the label, so the line fits the sidebar).
// Only once the API has said; no numbers to stand in meanwhile.
export function SidebarStorage() {
  const usage = useModels().data?.usage;
  if (!usage) return null;
  return (
    <>
      <Eyebrow>Model storage</Eyebrow>
      <StorageMeter usage={usage} />
      <span className="font-mono text-xs/[normal] whitespace-nowrap">
        {formatUsage(usage.used_bytes, usage.quota_bytes)}
      </span>
    </>
  );
}
