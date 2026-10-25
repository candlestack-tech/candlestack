import type { ComponentProps } from 'react';

import { UploadIcon } from '@/components/icons';
import { QuickAction } from '@/components/QuickAction';

import { FrameworkMini } from './FrameworkMark';
import { UPLOAD_FRAMEWORKS } from './frameworks';

// The quick action that opens the upload dialog (mockups/app, .qa-c): the icon, "Upload model"
// and the marks of the formats it takes.
export function UploadCard(props: ComponentProps<'button'>) {
  return (
    <QuickAction
      icon={UploadIcon}
      title="Upload model"
      line={
        <span className="flex flex-wrap items-center gap-3">
          {UPLOAD_FRAMEWORKS.map((framework) => (
            <span key={framework} className="inline-flex items-center gap-[5px] text-xs/[1.5] text-muted-foreground">
              <FrameworkMini framework={framework} named={false} />
              {framework}
            </span>
          ))}
        </span>
      }
      {...props}
    />
  );
}
