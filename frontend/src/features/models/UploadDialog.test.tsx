// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { catalogAnswer, manifest, MB, model, modelsAnswer, problem, stubApi } from '@/test/api';
import { addButton, file, jsonFile, LSTM, openUpload, PUT, READY, steps, UPLOAD, type Answers } from '@/test/upload';

import { UploadDialog } from './UploadDialog';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// The dialog on another page than Models, as the dashboard has it.
function renderElsewhere() {
  const root = createRootRoute({
    component: () => (
      <>
        <UploadDialog>
          <button type="button">Upload</button>
        </UploadDialog>
        <Outlet />
      </>
    ),
  });
  const page = (path: string) => createRoute({ getParentRoute: () => root, path });
  const router = createRouter({
    routeTree: root.addChildren([page('/dashboard'), page('/models/$modelId')]),
    history: createMemoryHistory({ initialEntries: ['/dashboard'] }),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe('the upload, step 1: the model', () => {
  it('opens on the model with the later steps waiting and Add model off', async () => {
    const { dialog } = await openUpload();
    expect(steps(dialog)).toEqual(['Upload the model', 'Upload the manifest', 'Add the model']);
    expect(dialog.getByText('A trained model in Keras (.keras) or ONNX (.onnx), up to 50 MB.')).toBeDefined();
    const zone = dialog.getByRole('button', { name: /Drop the model here/ });
    expect(zone.textContent).toBe('Drop the model hereor click to choose a fileKerasONNX');
    expect(dialog.getByRole('link', { name: 'How to export' }).getAttribute('href')).toBe(
      'https://onnx.ai/onnx/intro/converters.html',
    );
    expect(dialog.getByText(/^PyTorch, TensorFlow or scikit-learn\? Export the model to ONNX first\./)).toBeDefined();
    const input = dialog.getByLabelText<HTMLInputElement>('Model file');
    expect([input.type, input.accept]).toEqual(['file', '.keras,.onnx']);
    expect(addButton(dialog).disabled).toBe(true);
    // Off, it drops the accent for the muted surface, so it does not look like a button to press.
    expect(addButton(dialog).className).toContain('disabled:bg-surface-2');
    expect(addButton(dialog).className).toContain('disabled:text-muted-foreground');
  });

  it('opens the file picker on a click of the drop zone', async () => {
    const { user, dialog } = await openUpload();
    const click = vi.spyOn(dialog.getByLabelText<HTMLInputElement>('Model file'), 'click');
    await user.click(dialog.getByRole('button', { name: /Drop the model here/ }));
    expect(click).toHaveBeenCalledOnce();
  });

  it('marks the drop zone while a file is dragged over it and takes the file dropped', async () => {
    const { dialog, fetch } = await openUpload({ [UPLOAD]: { status: 201, body: LSTM } });
    const zone = dialog.getByRole('button', { name: /Drop the model here/ });
    fireEvent.dragEnter(zone);
    expect(zone.dataset.dragging).toBe('true');
    fireEvent.dragLeave(zone);
    expect(zone.dataset.dragging).toBeUndefined();
    fireEvent.drop(zone, { dataTransfer: { files: [file('lstm-signals.keras')] } });

    expect(await dialog.findByRole('heading', { name: 'Done: Model uploaded' })).toBeDefined();
    expect(fetch).toHaveBeenCalledWith('/api/v1/models?name=lstm-signals.keras', expect.anything());
  });

  it('sends an ONNX model to the API', async () => {
    const mixer = { ...LSTM, name: 'mixer.onnx', framework: 'onnx' as const };
    const { dialog, fetch, choose } = await openUpload({
      'POST /api/v1/models?name=mixer.onnx': { status: 201, body: mixer },
    });
    choose(file('mixer.onnx'));

    expect(await dialog.findByRole('heading', { name: 'Done: Model uploaded' })).toBeDefined();
    expect(fetch).toHaveBeenCalledWith('/api/v1/models?name=mixer.onnx', expect.anything());
  });

  it.each([
    [
      'a PyTorch model',
      'tcn.pt',
      'PyTorch files are not supported',
      'Export the model to ONNX. CandleStack takes Keras (.keras) and ONNX (.onnx) models.',
    ],
    [
      'any other file',
      'notes.png',
      'This file is not supported',
      'CandleStack takes Keras (.keras) and ONNX (.onnx) models.',
    ],
  ])('turns away %s at once, without asking the API', async (_, name, title, line) => {
    const { dialog, fetch, choose } = await openUpload();
    const asked = fetch.mock.calls.length;
    choose(file(name));
    const alert = dialog.getByRole('alert');
    expect(alert.textContent).toBe(`${title}${line}Choose another file`);
    expect(steps(dialog)[0]).toBe('Failed: Upload the model');
    expect(fetch.mock.calls.length).toBe(asked);
  });

  it('says a file over the limit is too large without sending it', async () => {
    const { fetch, dialog, choose } = await openUpload();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/v1/models', expect.anything()));
    choose(file('lstm-signals.keras', 61.2));
    expect(dialog.getByRole('alert').textContent).toBe('Larger than 50 MBThe file is 61.2 MB.Choose another file');
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it('shows the file being checked, then what the model takes and gives', async () => {
    let answer = (_value: { status: number; body: unknown }) => undefined as void;
    const { dialog, choose, fetch } = await openUpload({
      [UPLOAD]: () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });
    const chosen = file('lstm-signals.keras');
    choose(chosen);
    expect(steps(dialog)[0]).toBe('Checking: Upload the model');
    expect(dialog.getByText('Keras · 12.4 MB')).toBeDefined();
    expect(dialog.queryByRole('button', { name: /Drop the model here/ })).toBeNull();

    answer({ status: 201, body: LSTM });
    expect(await dialog.findByRole('heading', { name: 'Done: Model uploaded' })).toBeDefined();
    const facts = [...dialog.getAllByRole('term')].map(
      (term) => `${term.textContent}=${term.nextSibling?.textContent}`,
    );
    expect(facts).toEqual(['Format=Keras', 'Input=[25, 5]', 'Output=[1]', 'Size=12.4 MB']);
    expect(steps(dialog)).toEqual(['Done: Model uploaded', 'Upload the manifest', 'Add the model']);
    const init = fetch.mock.calls.find(([, options]) => options?.method === 'POST')?.[1];
    expect(init?.body).toBe(chosen);
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/octet-stream' });
  });

  // Once the file is in, the API checks and keeps it whatever the page does: closing the dialog
  // lets the upload come to its answer, and the model the API kept, never added, is deleted then.
  it('deletes the model the API kept after the dialog closed during its checks', async () => {
    let answer = (_value: { status: number; body: unknown }) => undefined as void;
    let request: RequestInit | undefined;
    const { dialog, choose, user, fetch } = await openUpload({
      [UPLOAD]: (init) => {
        request = init;
        return new Promise((resolve) => {
          answer = resolve;
        });
      },
      'DELETE /api/v1/models/mdl_a2b3c4d5e6': { status: 204 },
    });
    choose(file('lstm-signals.keras'));
    await vi.waitFor(() => expect(request).toBeDefined());
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(request?.signal?.aborted ?? false).toBe(false);
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);

    answer({ status: 201, body: LSTM });
    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/models/mdl_a2b3c4d5e6',
        expect.objectContaining({ method: 'DELETE' }),
      ),
    );
  });

  it.each([
    ['a server error', { status: 503, body: { detail: 'Down.' } }, 1],
    ['a refusal of the file', problem(413, 'model-too-large', 'Too large.'), 0],
    ['a failed check', problem(422, 'model-check-failed', 'No weights.', { check: 'weights' }), 0],
  ])('after %s asks for the models again only when the API may have kept the file', async (_, answer, again) => {
    const { fetch, dialog, choose } = await openUpload({ [UPLOAD]: answer });
    const listed = () => fetch.mock.calls.filter(([path]) => path === '/api/v1/models').length;
    await vi.waitFor(() => expect(listed()).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 20));
    const before = listed();
    choose(file('lstm-signals.keras'));
    await dialog.findByRole('button', { name: 'Choose another file' });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(listed()).toBe(before + again);
  });
});

describe('a refused model', () => {
  async function failWith(answer: Answers[string], answers: Answers = {}) {
    const opened = await openUpload({ [UPLOAD]: answer, ...answers });
    opened.choose(file('lstm-signals.keras'));
    const alert = await opened.dialog.findByRole('alert');
    return { ...opened, text: alert.textContent?.replace(/Choose another file$/, '') };
  }

  it.each([
    [
      'without trained weights',
      problem(422, 'model-check-failed', 'No weights.', { check: 'weights' }),
      'No trained weights in the fileNo weights.',
    ],
    [
      'that does not open',
      problem(422, 'model-check-failed', 'The file holds custom code (a Lambda layer).', { check: 'opens' }),
      'Does not open as a safe modelThe file holds custom code (a Lambda layer).',
    ],
    ['too large', problem(413, 'model-too-large', 'The file is 61.2 MB.'), 'Larger than 50 MBThe file is 61.2 MB.'],
    [
      'with the storage full',
      problem(409, 'storage-full', 'Full.', { used_bytes: Math.round(92.3 * MB), quota_bytes: 100 * MB }),
      'Not enough storage92.3 MB of 100 MB used. Delete a model to free space.',
    ],
    [
      'while another upload runs',
      problem(409, 'upload-in-progress', 'Wait for it to finish.'),
      'Another upload is in progressWait for it to finish.',
    ],
    ['with too many models', problem(409, 'too-many-models', 'Delete one first.'), 'Too many modelsDelete one first.'],
    ['that took too long', problem(408, 'upload-timeout', 'Try again.'), 'Upload timed outTry again.'],
    ['for another reason', { status: 500, body: { detail: 'Broke.' } }, 'Upload failedBroke.'],
    [
      "by the API's check of the request",
      problem(422, 'validation', "query parameter 'name': too long"),
      'Upload failedThe file could not be sent. Rename it and try again.',
    ],
    [
      'of a format the API does not take',
      problem(415, 'unsupported-model-format', 'Keras only.'),
      'This file is not supportedCandleStack takes Keras (.keras) and ONNX (.onnx) models.',
    ],
    [
      'without an answer',
      () => Promise.reject(new TypeError('Failed to fetch')),
      'Upload failedCannot reach the server. Check your connection.',
    ],
  ])('%s says why in its step', async (_, answer, text) => {
    const failed = await failWith(answer);
    expect(failed.text).toBe(text);
    expect(steps(failed.dialog)[0]).toBe('Failed: Upload the model');
  });

  it('with the storage full and no numbers in the answer: the numbers the app has', async () => {
    const { dialog, choose } = await openUpload({
      ...modelsAnswer([model({ size_bytes: 92 * MB })]),
      [UPLOAD]: problem(409, 'storage-full', 'Full.'),
    });
    await vi.waitFor(() => expect(screen.getByText(/^1 model · 92 MB of 100 MB used/)).toBeDefined());
    choose(file('lstm-signals.keras'));
    expect((await dialog.findByRole('alert')).textContent).toBe(
      'Not enough storage92 MB of 100 MB used. Delete a model to free space.Choose another file',
    );
  });

  it('offers another file: back to the drop zone, with the file picker open', async () => {
    const { dialog, user } = await failWith(problem(413, 'model-too-large', 'Too large.'));
    const click = vi.spyOn(dialog.getByLabelText<HTMLInputElement>('Model file'), 'click');
    await user.click(dialog.getByRole('button', { name: 'Choose another file' }));
    expect(dialog.getByRole('button', { name: /Drop the model here/ })).toBeDefined();
    expect(click).toHaveBeenCalledOnce();
  });
});

describe('Add model elsewhere', () => {
  it("opens the model's page on Add model when it was opened elsewhere", async () => {
    stubApi({
      ...modelsAnswer([]),
      ...catalogAnswer,
      [UPLOAD]: { status: 201, body: LSTM },
      [PUT]: { status: 200, body: READY },
    });
    const router = renderElsewhere();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Upload' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Add a model' }));
    fireEvent.change(dialog.getByLabelText('Model file'), { target: { files: [file('lstm-signals.keras')] } });
    await dialog.findByRole('heading', { name: 'Done: Model uploaded' });
    fireEvent.change(dialog.getByLabelText('Manifest file'), {
      target: { files: [jsonFile('lstm.manifest.json', manifest())] },
    });
    await dialog.findByRole('heading', { name: 'Done: Manifest uploaded' });
    await user.click(addButton(dialog));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/models/mdl_a2b3c4d5e6'));
  });
});
