// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Model } from '@/api';
import { ARSEN, catalogAnswer, manifest, MB, modelsAnswer, problem, sentBody, session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { addButton, file, jsonFile, LSTM, openUpload, PUT, READY, steps, UPLOAD, type Answers } from '@/test/upload';

const DELETE = 'DELETE /api/v1/models/mdl_a2b3c4d5e6';

// The API's list as it changes: the upload keeps the model, a DELETE that succeeds removes it.
function keptUntilDeleted(deleted: { status: number; body?: unknown } = { status: 204 }): Answers {
  let list: Model[] = [];
  return {
    [UPLOAD]: async () => {
      list = [LSTM];
      return { status: 201, body: LSTM };
    },
    'GET /api/v1/models': async () => modelsAnswer(list)['GET /api/v1/models'] as { status: number; body: unknown },
    [DELETE]: async () => {
      if (deleted.status < 300) list = [];
      return deleted;
    },
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('the upload, step 2: the manifest', () => {
  async function uploaded(answers: Answers = {}) {
    const opened = await openUpload({ [UPLOAD]: { status: 201, body: LSTM }, ...answers });
    opened.choose(file('lstm-signals.keras'));
    await opened.dialog.findByRole('heading', { name: 'Done: Model uploaded' });
    return opened;
  }

  it('asks for the manifest with a link to its format: a model needs one to be added', async () => {
    const { dialog } = await uploaded();
    expect(dialog.getByRole('link', { name: 'About the manifest format' }).getAttribute('href')).toBe(
      'https://candlestack.tech/docs/manifest',
    );
    expect(dialog.getByRole('button', { name: /Drop manifest\.json here/ })).toBeDefined();
    expect(dialog.getByLabelText<HTMLInputElement>('Manifest file').accept).toBe('.json,application/json');
    expect(dialog.queryByRole('button', { name: /without a manifest/ })).toBeNull();
    expect(dialog.queryByText(/without a manifest/)).toBeNull();
    expect(addButton(dialog).disabled).toBe(true);
  });

  it('sends the manifest, then shows what it reads and that it fits the model', async () => {
    const { dialog, chooseManifest, fetch, user } = await uploaded({ [PUT]: { status: 200, body: READY } });
    chooseManifest(jsonFile('lstm.manifest.json', manifest()));

    expect(await dialog.findByRole('heading', { name: 'Done: Manifest uploaded' })).toBeDefined();
    expect(sentBody(fetch, PUT)).toEqual(manifest());
    expect(dialog.getByText('lstm.manifest.json')).toBeDefined();
    const terms = dialog.getAllByRole('term').map((term) => `${term.textContent}=${term.nextSibling?.textContent}`);
    expect(terms.slice(4)).toEqual(['Input=[25, 5]', 'Output=[1]', 'Reads=Candles · 30m', 'Warm-up=24 candles']);

    const match = dialog.getByRole('button', { name: /model \[25, 5\] = manifest \[25, 5\]/ });
    expect(match.getAttribute('aria-expanded')).toBe('false');
    await user.click(match);
    expect(match.getAttribute('aria-expanded')).toBe('true');
    const column = dialog.getByRole('button', { name: 'Column 4, close' });
    await user.click(column);
    expect(column.getAttribute('aria-pressed')).toBe('true');
    expect(await dialog.findByText('The close of the candle')).toBeDefined();

    expect(steps(dialog)[2]).toBe('Add the model');
    expect(dialog.getByText('Candles, 30m · 25 × 5')).toBeDefined();
    expect(addButton(dialog).disabled).toBe(false);
  });

  it('shows the problems of a manifest the API refused in its step, with how many more there are', async () => {
    const { dialog, chooseManifest } = await uploaded({
      [PUT]: problem(422, 'manifest-check-failed', 'Wrong.', {
        problems: [
          { check: 'model', detail: 'The manifest gives [24, 5], the model takes [25, 5].' },
          { check: 'names', detail: 'The feature name open is used 2 times.' },
        ],
        more_problems: 3,
        unknown_blocks: [],
      }),
    });
    chooseManifest(jsonFile('old.manifest.json', manifest(24)));
    const alert = await dialog.findByRole('alert');
    expect([...alert.children].map((child) => child.textContent)).toEqual([
      'The manifest gives [24, 5], the model takes [25, 5].',
      'The feature name open is used 2 times.',
      'And 3 more problems.',
      'Choose another file',
    ]);
    expect(steps(dialog)[1]).toBe('Failed: Upload the manifest');
    expect(addButton(dialog).disabled).toBe(true);
  });

  it.each([
    [
      'a file that is no JSON',
      () => new File(['not json {'], 'notes.json'),
      'This file is not JSONChoose the manifest, a .json file.',
    ],
    [
      'a file over 1 MB',
      () => {
        const big = jsonFile('big.json', manifest());
        Object.defineProperty(big, 'size', { value: 1.5 * MB });
        return big;
      },
      'Larger than 1 MBThe file is 1.5 MB.',
    ],
  ])('turns away %s without sending it', async (_, make, text) => {
    const { dialog, chooseManifest, fetch } = await uploaded();
    chooseManifest(make());
    expect((await dialog.findByRole('alert')).textContent).toBe(`${text}Choose another file`);
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
  });

  it('offers another manifest, with the file picker open', async () => {
    const { dialog, chooseManifest, user } = await uploaded();
    chooseManifest(new File(['{'], 'broken.json'));
    await dialog.findByRole('alert');
    const click = vi.spyOn(dialog.getByLabelText<HTMLInputElement>('Manifest file'), 'click');
    await user.click(dialog.getByRole('button', { name: 'Choose another file' }));
    expect(dialog.getByRole('button', { name: /Drop manifest\.json here/ })).toBeDefined();
    expect(click).toHaveBeenCalledOnce();
  });

  it("opens the model's page on Add model and keeps the model", async () => {
    const { dialog, user, router, fetch, chooseManifest } = await uploaded({ [PUT]: { status: 200, body: READY } });
    chooseManifest(jsonFile('lstm.manifest.json', manifest()));
    await dialog.findByRole('heading', { name: 'Done: Manifest uploaded' });
    await user.click(addButton(dialog));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/models/mdl_a2b3c4d5e6'));
    expect(screen.queryByRole('dialog', { name: 'Add a model' })).toBeNull();
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  });

  it.each([
    ['before the manifest', false],
    ['with the manifest kept but before Add model', true],
  ])('deletes the uploaded model when the dialog is cancelled %s', async (_, withManifest) => {
    const { dialog, user, fetch, chooseManifest } = await uploaded({
      ...keptUntilDeleted(),
      [PUT]: { status: 200, body: READY },
    });
    if (withManifest) {
      chooseManifest(jsonFile('lstm.manifest.json', manifest()));
      await dialog.findByRole('heading', { name: 'Done: Manifest uploaded' });
    }
    // Behind the dialog, so hidden from the accessibility tree while it is open.
    expect(screen.getAllByRole('article', { hidden: true })).toHaveLength(1);
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));

    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/models/mdl_a2b3c4d5e6',
        expect.objectContaining({ method: 'DELETE' }),
      ),
    );
    await vi.waitFor(() => expect(screen.queryAllByRole('article')).toHaveLength(0));
  });

  it('asks for the list again when the model could not be deleted, so it shows', async () => {
    const { dialog, user, fetch } = await uploaded(keptUntilDeleted({ status: 503, body: { detail: 'Down.' } }));
    const listed = () => fetch.mock.calls.filter(([path]) => path === '/api/v1/models').length;
    // The list asked for again after the upload first.
    await new Promise((resolve) => setTimeout(resolve, 20));
    const before = listed();
    await user.click(dialog.getByRole('button', { name: 'Close' }));
    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/models/mdl_a2b3c4d5e6',
        expect.objectContaining({ method: 'DELETE' }),
      ),
    );
    await vi.waitFor(() => expect(listed()).toBeGreaterThan(before));
    expect(screen.getAllByRole('article')).toHaveLength(1);
  });
});

describe('Add manifest', () => {
  it('starts from the model, keeps the manifest and marks the tile ready', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([LSTM]), ...catalogAnswer });
    await renderApp('/models');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Add manifest to lstm-signals.keras' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Add a manifest to lstm-signals.keras' }));
    expect(steps(dialog)).toEqual(['Done: The model', 'Upload the manifest', 'Save']);

    const fetch = stubApi({ ...modelsAnswer([READY]), ...catalogAnswer, [PUT]: { status: 200, body: READY } });
    fireEvent.change(dialog.getByLabelText('Manifest file'), {
      target: { files: [jsonFile('lstm.manifest.json', manifest())] },
    });
    expect(await dialog.findByRole('heading', { name: 'Done: Manifest uploaded' })).toBeDefined();
    expect(steps(dialog)[2]).toBe('Save the manifest');
    expect(sentBody(fetch, PUT)).toEqual(manifest());

    await user.click(dialog.getByRole('button', { name: 'Save' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(within(screen.getByRole('article')).getByText('Ready')).toBeDefined();
  });

  it('keeps the model when the dialog is cancelled', async () => {
    const fetch = stubApi({ ...session(ARSEN), ...modelsAnswer([LSTM]), ...catalogAnswer });
    await renderApp('/models');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Add manifest to lstm-signals.keras' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Add a manifest to lstm-signals.keras' }));
    await user.click(dialog.getByRole('button', { name: 'Cancel' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    expect(screen.getAllByRole('article')).toHaveLength(1);
  });
});
