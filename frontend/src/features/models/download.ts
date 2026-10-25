import type { Manifest, Model } from '@/api';

import { splitExtension } from './format';

// Saves the manifest as the model's own file, `lstm.manifest.json`, as it was given.
export function downloadManifest(model: Model, manifest: Manifest) {
  const blob = new Blob([`${JSON.stringify(manifest, null, 2)}\n`], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${splitExtension(model.name)[0]}.manifest.json`;
  link.click();
  URL.revokeObjectURL(url);
}
