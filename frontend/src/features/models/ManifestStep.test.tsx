// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ARSEN, catalogAnswer, manifest, MB, modelsAnswer, problem, sentBody, session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { addButton, file, jsonFile, LSTM, openUpload, PUT, READY, steps, UPLOAD, type Answers } from '@/test/upload';

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

  it('asks for the manifest with a link to its format, or lets the model go without one', async () => {
    const { dialog } = await uploaded();
    expect(dialog.getByRole('link', { name: 'About the manifest format' }).getAttribute('href')).toBe(
      'https://candlestack.tech/docs/manifest',
    );
    expect(dialog.getByRole('button', { name: /Drop manifest\.json here/ })).toBeDefined();
    expect(dialog.getByLabelText<HTMLInputElement>('Manifest file').accept).toBe('.json,application/json');
    expect(dialog.getByRole('button', { name: 'Add the model without a manifest' })).toBeDefined();
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

  it('adds the model without a manifest as Needs setup, or takes one after all', async () => {
    const { dialog, user } = await uploaded();
    await user.click(dialog.getByRole('button', { name: 'Add the model without a manifest' }));
    expect(steps(dialog)).toEqual(['Done: Model uploaded', 'Done: No manifest for now', 'Add the model']);
    expect(dialog.getByText(/goes to Models as/).textContent).toBe(
      'lstm-signals.keras goes to Models as Needs setup. It can run once it has a manifest.',
    );
    expect(addButton(dialog).disabled).toBe(false);

    await user.click(dialog.getByRole('button', { name: 'Add it now' }));
    expect(dialog.getByRole('button', { name: /Drop manifest\.json here/ })).toBeDefined();
    expect(addButton(dialog).disabled).toBe(true);
  });

  it("opens the model's page on Add model", async () => {
    const { dialog, user, router } = await uploaded({ [PUT]: { status: 200, body: READY } });
    await user.click(dialog.getByRole('button', { name: 'Add the model without a manifest' }));
    await user.click(addButton(dialog));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/models/mdl_a2b3c4d5e6'));
    expect(screen.queryByRole('dialog', { name: 'Add a model' })).toBeNull();
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
    expect(dialog.queryByRole('button', { name: 'Add the model without a manifest' })).toBeNull();

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
});
