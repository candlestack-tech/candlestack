import { useState } from 'react';

import type { Manifest, Model } from '@/api';
import { ACCENT_BUTTON, BUTTON_SIZES, GHOST_BUTTON, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { DeleteDialog } from './DeleteDialog';
import { downloadManifest } from './download';
import { RenameDialog } from './RenameDialog';

const SMALL = cn(BUTTON_SIZES.sm, 'font-medium');

type Props = {
  model: Model;
  // The manifest once it is loaded; Download manifest waits for it.
  manifest: Manifest | undefined;
  onBacktest: () => void;
  onAddManifest: () => void;
  onDeleted: () => void;
};

type Open = 'rename' | 'delete';

const MAIN = cn(SMALL, ACCENT_BUTTON);

// Backtest with it and Download manifest, or Add manifest while the model has none.
function ManifestButtons({ model, manifest, onBacktest, onAddManifest }: Omit<Props, 'onDeleted'>) {
  if (model.state === 'needs_setup') {
    return (
      <Button type="button" className={MAIN} onClick={onAddManifest}>
        Add manifest
      </Button>
    );
  }
  return (
    <>
      <Button type="button" className={MAIN} onClick={onBacktest}>
        Backtest with it
      </Button>
      <Button
        type="button"
        variant="outline"
        className={cn(SMALL, OUTLINE_BUTTON)}
        disabled={!manifest}
        onClick={() => manifest && downloadManifest(model, manifest)}
      >
        Download manifest
      </Button>
    </>
  );
}

// The buttons of a model's page (mockups/app, .p5-acts): those of its manifest, then Rename and
// Delete, which turns red on hover, with their dialogs.
export function ModelActions({ onDeleted, ...props }: Props) {
  const [open, setOpen] = useState<Open | null>(null);
  const dialog = (kind: Open) => ({
    model: props.model,
    open: open === kind,
    onOpenChange: (value: boolean) => setOpen(value ? kind : null),
  });
  const ghost = cn(SMALL, GHOST_BUTTON);
  return (
    <>
      <ManifestButtons {...props} />
      <Button type="button" variant="ghost" className={ghost} onClick={() => setOpen('rename')}>
        Rename
      </Button>
      <Button type="button" variant="ghost" className={cn(ghost, 'hover:text-down')} onClick={() => setOpen('delete')}>
        Delete
      </Button>
      <RenameDialog {...dialog('rename')} />
      <DeleteDialog {...dialog('delete')} onDeleted={onDeleted} />
    </>
  );
}
