// What the tests of the upload share: a model and its answers, files, and the dialog opened on the
// Models page.
import { fireEvent, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';

import { ARSEN, catalogAnswer, MB, model, modelsAnswer, rawCandlesSummary, session, stubApi } from './api';
import { renderApp } from './app';

export const UPLOAD = 'POST /api/v1/models?name=lstm-signals.keras';
export const PUT = 'PUT /api/v1/models/mdl_a2b3c4d5e6/manifest';
export const LSTM = model({
  id: 'mdl_a2b3c4d5e6',
  name: 'lstm-signals.keras',
  size_bytes: Math.round(12.4 * MB),
  input_shape: [25, 5],
});
export const READY = model({ ...LSTM, state: 'ready', manifest_summary: rawCandlesSummary() });

// A file of `mb` megabytes; only its size and name matter.
export function file(name: string, mb = 12.4) {
  const item = new File(['x'], name);
  Object.defineProperty(item, 'size', { value: Math.round(mb * MB) });
  return item;
}

export const jsonFile = (name: string, body: unknown) => new File([JSON.stringify(body)], name);

export type Answers = Parameters<typeof stubApi>[0];

export async function openUpload(answers: Answers = {}) {
  const fetch = stubApi({ ...session(ARSEN), ...modelsAnswer([]), ...catalogAnswer, ...answers });
  const app = await renderApp('/models');
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: /^Upload model/ }));
  const dialog = within(screen.getByRole('dialog', { name: 'Add a model' }));
  const choose = (item: File) => fireEvent.change(dialog.getByLabelText('Model file'), { target: { files: [item] } });
  const chooseManifest = (item: File) =>
    fireEvent.change(dialog.getByLabelText('Manifest file'), { target: { files: [item] } });
  return { ...app, fetch, user, dialog, choose, chooseManifest };
}

// The steps as screen readers hear their titles.
export const steps = (dialog: { getAllByRole: (role: 'heading', options: { level: number }) => HTMLElement[] }) =>
  dialog.getAllByRole('heading', { level: 3 }).map((item) => item.textContent?.trim());

export const addButton = (dialog: ReturnType<typeof within>) =>
  dialog.getByRole('button', { name: 'Add model' }) as HTMLButtonElement;
