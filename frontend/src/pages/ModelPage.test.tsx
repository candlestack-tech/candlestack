// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ARSEN,
  backtest,
  backtestsAnswer,
  catalogAnswer,
  DEFAULT_PREFERENCES,
  failedBacktest,
  manifest,
  MB,
  model,
  modelsAnswer,
  problem,
  rawCandlesSummary,
  session,
  stubApi,
} from '@/test/api';
import { renderApp } from '@/test/app';

const ID = 'mdl_7f3k2m9qx4';
const PAGE = `/models/${ID}`;
const MANIFEST = `GET /api/v1/models/${ID}/manifest`;
const READY = model({
  state: 'ready',
  input_shape: [25, 5],
  manifest_summary: rawCandlesSummary(),
  backtest_count: 2,
  best_sharpe: 0.79,
});
const SETUP = model({ input_shape: [25, 5] });
const OTHER = model({ id: 'mdl_a2b3c4d5e6', name: 'lstm-signals.keras', size_bytes: 3 * MB });
const BACKTESTS = `?model=${encodeURIComponent(READY.name)}`;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T12:00:00Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

type Answers = Parameters<typeof stubApi>[0];

async function openPage(item = READY, answers: Answers = {}) {
  const fetch = stubApi({
    ...session(ARSEN),
    ...modelsAnswer([item, OTHER]),
    ...catalogAnswer,
    [MANIFEST]: { status: 200, body: manifest() },
    ...backtestsAnswer({ backtests: [] }, BACKTESTS),
    ...answers,
  });
  const app = await renderApp(PAGE);
  await screen.findByRole('heading', { level: 1, name: item.name });
  return { ...app, fetch, user: userEvent.setup() };
}

const box = (name: string) =>
  within(screen.getByRole('heading', { level: 2, name: new RegExp(`^${name}`) }).closest('section') ?? document.body);
const facts = () =>
  screen
    .getAllByRole('term')
    .slice(0, 4)
    .map((term) => `${term.textContent}=${term.nextSibling?.textContent}`);

describe("a model's page", () => {
  it('shows the model with its state, what it reads and its results, under a link back to Models', async () => {
    await openPage();
    expect(document.title).toBe('my-tsmixer.keras · CandleStack');
    expect(screen.getByRole('link', { name: '← Models' }).getAttribute('href')).toBe('/models');
    expect(screen.getByText('Keras · 14.2 MB · added 13 Oct')).toBeDefined();
    expect(screen.getByText('Ready')).toBeDefined();
    expect(facts()).toEqual(['Reads=Candles · 30m', 'Warm-up=24 candles', 'Backtests=2', 'Best Sharpe=0.79']);
    expect(
      ['Backtest with it', 'Download manifest', 'Rename', 'Delete'].map((name) => screen.getByRole('button', { name })),
    ).toHaveLength(4);
  });

  it('draws the input as a matrix and shows what a column is on a click', async () => {
    const { user } = await openPage();
    const input = box('Input');
    expect(await input.findByText('[25, 5]')).toBeDefined();
    expect(input.getByText('25 rows')).toBeDefined();
    expect(input.getByText('5 columns')).toBeDefined();
    expect(input.getByText('Click a column to see what it is.')).toBeDefined();
    const columns = within(input.getByRole('toolbar', { name: 'Input columns' })).getAllByRole('button');
    expect(columns.map((column) => column.getAttribute('aria-label'))).toEqual([
      'Column 1, open',
      'Column 2, high',
      'Column 3, low',
      'Column 4, close',
      'Column 5, volume',
    ]);
    // Without an example, every cell has the same tint: no values to draw.
    const tints = new Set(
      [...input.getByRole('toolbar', { name: 'Input columns' }).querySelectorAll('i')].map((cell) =>
        cell.style.getPropertyValue('--a'),
      ),
    );
    expect([...tints]).toEqual(['18%']);

    await user.click(columns[4] as HTMLElement);
    expect(columns[4]?.getAttribute('aria-pressed')).toBe('true');
    const terms = input.getAllByRole('term').map((term) => `${term.textContent}=${term.nextSibling?.textContent}`);
    expect(terms).toEqual(['5 · volume=The volume of the candle', 'Settings=none', 'Scaling=as is']);
    await user.click(columns[4] as HTMLElement);
    expect(input.getByText('Click a column to see what it is.')).toBeDefined();
  });

  it('moves between the columns with the arrow keys, one tab stop for all', async () => {
    const { user } = await openPage();
    const columns = within(await screen.findByRole('toolbar', { name: 'Input columns' })).getAllByRole('button');
    expect(columns.map((column) => column.tabIndex)).toEqual([0, -1, -1, -1, -1]);
    columns[0]?.focus();
    await user.keyboard('{ArrowRight}{ArrowRight}');
    expect(document.activeElement).toBe(columns[2]);
    await user.keyboard('{End}');
    expect(document.activeElement).toBe(columns[4]);
    await user.keyboard('{Home}');
    expect(document.activeElement).toBe(columns[0]);
  });

  it("shades the cells by the manifest's example", async () => {
    const rows = Array.from({ length: 25 }, (_, row) => [row, 0, 0, 0, 0]);
    await openPage(READY, {
      [MANIFEST]: { status: 200, body: manifest(25, { example: { input: [rows], output: [[0.1]] } }) },
    });
    const toolbar = await screen.findByRole('toolbar', { name: 'Input columns' });
    const open = [...(toolbar.firstElementChild?.querySelectorAll('i') ?? [])].map((cell) =>
      cell.style.getPropertyValue('--a'),
    );
    expect([open[0], open.at(-1)]).toEqual(['12%', '74%']);
  });

  it('lists the checks it passed and the ones still to come', async () => {
    await openPage();
    const checks = box('Checks')
      .getAllByRole('listitem')
      .map((item) => item.textContent);
    expect(checks).toEqual([
      'Passed: Opens, no custom code',
      'Passed: Trained weights',
      'Passed: One input, one output',
      'Passed: Manifest fits [25, 5]',
      'Not yet: Trial runNot run yet',
      'Not yet: No example',
    ]);
  });

  it('passes every check once verified', async () => {
    await openPage(model({ ...READY, state: 'verified' }));
    expect(screen.getByText('Verified')).toBeDefined();
    expect(box('Checks').getAllByRole('listitem').at(-1)?.textContent).toBe('Passed: Example matches');
  });

  it('downloads the manifest as the model’s own file', async () => {
    const { user } = await openPage();
    // jsdom has no object URLs: given for this test only.
    const create = vi.fn((_blob: Blob) => 'blob:manifest');
    Object.defineProperty(URL, 'createObjectURL', { value: create, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const download = screen.getByRole<HTMLButtonElement>('button', { name: 'Download manifest' });
    await vi.waitFor(() => expect(download.disabled).toBe(false));
    await user.click(download);
    expect(click).toHaveBeenCalledOnce();
    expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('my-tsmixer.manifest.json');
    const blob = create.mock.calls[0]?.[0];
    if (!blob) throw new Error('No file was made');
    expect(JSON.parse(await blob.text())).toEqual(manifest());
    click.mockRestore();
    Reflect.deleteProperty(URL, 'createObjectURL');
    Reflect.deleteProperty(URL, 'revokeObjectURL');
  });

  it("lists the model's backtests, newest first, with a link to all of them when there are more", async () => {
    const one = backtest({ id: 'bt_1', number: 12, model: { id: ID, name: READY.name, framework: 'keras' } });
    const two = failedBacktest({ id: 'bt_2', number: 9 });
    const { fetch } = await openPage(READY, {
      ...backtestsAnswer({ backtests: [one, two], total: 14, pages: 2 }, BACKTESTS),
    });
    const runs = box('Backtests');
    expect((await runs.findByRole('link', { name: 'Backtest #12' })).getAttribute('href')).toBe('/backtests/bt_1');
    expect(runs.getByRole('heading', { level: 2 }).textContent).toBe('Backtests 14');
    expect(runs.getByRole('link', { name: 'All 14' }).getAttribute('href')).toBe('/backtests?model=my-tsmixer.keras');
    expect(runs.getByText('+24.6 %')).toBeDefined();
    expect(runs.getByText('Failed')).toBeDefined();
    expect(fetch).toHaveBeenCalledWith(`/api/v1/backtests${BACKTESTS}`, expect.anything());
  });

  it('says when no backtest used the model yet', async () => {
    await openPage();
    expect(await box('Backtests').findByText('None yet')).toBeDefined();
  });

  it('opens the builder with the model chosen from Backtest with it', async () => {
    const { user, router } = await openPage(READY, {
      'GET /api/v1/me/preferences': { status: 200, body: DEFAULT_PREFERENCES },
    });
    await user.click(screen.getByRole('button', { name: 'Backtest with it' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'New backtest' })).toBeDefined();
    expect(router.state.location.search).toEqual({ model: READY.id });
    expect(screen.getByRole('button', { name: new RegExp(`^${READY.name}`) }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(screen.getByRole('link', { name: '← Models' })).toBeDefined();
  });

  it('says so for a model the user does not have', async () => {
    stubApi({ ...session(ARSEN), ...modelsAnswer([OTHER]) });
    await renderApp(PAGE);
    expect(await screen.findByRole('heading', { level: 1, name: 'Not found' })).toBeDefined();
    expect(screen.getByText('You have no model with this ID.')).toBeDefined();
  });

  it('says why the models could not load', async () => {
    stubApi({ ...session(ARSEN), 'GET /api/v1/models': { status: 503, body: { detail: 'Down for a minute.' } } });
    await renderApp(PAGE);
    expect(await screen.findByRole('heading', { level: 1, name: 'Model' })).toBeDefined();
    expect(screen.getByText('Down for a minute.')).toBeDefined();
  });
});

describe('a model without a manifest', () => {
  it('offers Add manifest in the header and in place of the input, and asks for no manifest', async () => {
    const { fetch, user } = await openPage(SETUP);
    expect(screen.getByText('Needs setup')).toBeDefined();
    expect(facts()).toEqual(['Input=[25, 5]', 'Backtests=0', 'Best Sharpe=–']);
    expect(screen.queryByRole('button', { name: 'Backtest with it' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Download manifest' })).toBeNull();
    expect(box('No manifest').getByRole('button', { name: 'Add manifest' })).toBeDefined();
    expect(
      box('Checks')
        .getAllByRole('listitem')
        .slice(3)
        .map((item) => item.textContent),
    ).toEqual(['Not yet: Manifest', 'Not yet: Trial run', 'Not yet: Example']);
    expect(fetch.mock.calls.some(([path]) => path.endsWith('/manifest'))).toBe(false);

    await user.click(screen.getAllByRole('button', { name: 'Add manifest' })[0] as HTMLElement);
    expect(await screen.findByRole('dialog', { name: 'Add a manifest to my-tsmixer.keras' })).toBeDefined();
  });
});

describe('Rename model', () => {
  async function openRename() {
    const opened = await openPage();
    await opened.user.click(screen.getByRole('button', { name: 'Rename' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Rename model' }));
    const input = dialog.getByRole<HTMLInputElement>('textbox', { name: 'Model name' });
    return { ...opened, dialog, input, save: dialog.getByRole<HTMLButtonElement>('button', { name: 'Save' }) };
  }

  it('edits the name without its extension, focused and selected', async () => {
    const { dialog, input, save } = await openRename();
    expect(input.value).toBe('my-tsmixer');
    expect(document.activeElement).toBe(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, 'my-tsmixer'.length]);
    expect(dialog.getByText('.keras')).toBeDefined();
    expect(
      dialog.getByText('The extension stays .keras. Backtests that used this model show the new name.'),
    ).toBeDefined();
    expect(save.disabled).toBe(true);
  });

  it('keeps Save off while the name is empty or unchanged', async () => {
    const { user, input, save } = await openRename();
    await user.clear(input);
    await user.type(input, '   ');
    expect(save.disabled).toBe(true);
    await user.clear(input);
    await user.type(input, ' my-tsmixer ');
    expect(save.disabled).toBe(true);
    await user.type(input, '2');
    expect(save.disabled).toBe(false);
  });

  it('saves on Enter: the new name in the header, the dialog closed', async () => {
    const { user, input } = await openRename();
    const renamed = model({ ...READY, name: 'tsmixer-v2.keras' });
    const fetch = stubApi({
      ...modelsAnswer([renamed, OTHER]),
      ...catalogAnswer,
      [MANIFEST]: { status: 200, body: manifest() },
      ...backtestsAnswer({ backtests: [] }, '?model=tsmixer-v2.keras'),
      [`PATCH /api/v1/models/${ID}`]: { status: 200, body: renamed },
    });
    await user.clear(input);
    await user.type(input, '  tsmixer-v2 {Enter}');

    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body).toBe('{"name":"tsmixer-v2"}');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('tsmixer-v2.keras');
  });

  it('stays on the page when asking for the list again fails', async () => {
    const { user, input } = await openRename();
    const renamed = model({ ...READY, name: 'tsmixer-v2.keras' });
    const fetch = stubApi({
      'GET /api/v1/models': { status: 503, body: { detail: 'Down for a minute.' } },
      ...catalogAnswer,
      [MANIFEST]: { status: 200, body: manifest() },
      ...backtestsAnswer({ backtests: [] }, '?model=tsmixer-v2.keras'),
      [`PATCH /api/v1/models/${ID}`]: { status: 200, body: renamed },
    });
    await user.clear(input);
    await user.type(input, 'tsmixer-v2{Enter}');

    await vi.waitFor(() => expect(fetch.mock.calls.some(([path]) => path === '/api/v1/models')).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('tsmixer-v2.keras');
    expect(screen.queryByText('Down for a minute.')).toBeNull();
  });

  it('turns the help line red when the user already has the name', async () => {
    const { user, dialog, input, save } = await openRename();
    stubApi({ [`PATCH /api/v1/models/${ID}`]: problem(409, 'model-name-taken', 'Taken.') });
    await user.clear(input);
    await user.type(input, 'lstm-signals');
    await user.click(save);

    expect((await dialog.findByRole('alert')).textContent).toBe('You already have a model with this name.');
    await user.type(input, '2');
    expect(dialog.queryByRole('alert')).toBeNull();
  });

  it.each([
    ['a slash', 'lstm/v2', 'Names cannot contain / or \\.'],
    ['a tab', 'lstm\tv2', 'Names cannot contain invisible characters such as tabs.'],
    ['61 characters', 'a'.repeat(61), 'Names can be up to 60 characters.'],
  ])('says at once that a name with %s cannot be used, with Save off and no request', async (_, name, text) => {
    const { user, dialog, input, save } = await openRename();
    const fetch = stubApi(modelsAnswer([READY, OTHER]));
    await user.clear(input);
    await user.paste(name);
    expect(dialog.getByRole('alert').textContent).toBe(text);
    expect(save.disabled).toBe(true);
    await user.keyboard('{Enter}');
    expect(fetch).not.toHaveBeenCalled();
  });

  it("says in plain words when the API refuses the name for another reason, not in the API's", async () => {
    const { user, dialog, input, save } = await openRename();
    stubApi({ [`PATCH /api/v1/models/${ID}`]: problem(422, 'validation', "body 'name': String should match pattern") });
    await user.type(input, '2');
    await user.click(save);
    expect((await dialog.findByRole('alert')).textContent).toBe('This name cannot be used. Try another one.');
  });

  it('closes and says the model is gone when it was deleted meanwhile', async () => {
    const { user, input, save } = await openRename();
    stubApi({
      ...modelsAnswer([OTHER]),
      [`PATCH /api/v1/models/${ID}`]: problem(404, 'not-found', 'You have no such model.'),
    });
    await user.type(input, '2');
    await user.click(save);
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(await screen.findByRole('heading', { level: 1, name: 'Not found' })).toBeDefined();
  });
});

describe('Delete model', () => {
  async function openDelete(item = READY) {
    const opened = await openPage(item);
    await opened.user.click(screen.getByRole('button', { name: 'Delete' }));
    return { ...opened, dialog: within(screen.getByRole('dialog', { name: `Delete ${item.name}?` })) };
  }

  it('says what goes, that the backtests that used it stay and that it cannot be undone', async () => {
    const { dialog } = await openDelete();
    expect(
      dialog.getByText(
        'The file goes from your models and frees 14.2 MB. The 2 backtests that used it stay, with their results. This cannot be undone.',
      ),
    ).toBeDefined();
  });

  it('deletes and goes back to Models, where the storage frees up', async () => {
    const { user, dialog, router } = await openDelete();
    const fetch = stubApi({ ...modelsAnswer([OTHER]), [`DELETE /api/v1/models/${ID}`]: { status: 204 } });
    await user.click(dialog.getByRole('button', { name: 'Delete' }));

    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/models'));
    expect(fetch).toHaveBeenCalledWith(`/api/v1/models/${ID}`, expect.objectContaining({ method: 'DELETE' }));
    expect(await screen.findByText(/^1 model · 3 MB of 100 MB used/)).toBeDefined();
  });

  it('stays open with the reason when the API cannot delete', async () => {
    const { user, dialog } = await openDelete();
    stubApi({ [`DELETE /api/v1/models/${ID}`]: { status: 503, body: { detail: 'Down for a minute.' } } });
    await user.click(dialog.getByRole('button', { name: 'Delete' }));
    expect((await dialog.findByRole('alert')).textContent).toBe('Down for a minute.');
  });
});

describe('the dialogs of a model', () => {
  it('give the focus back to the button that opened them when they close', async () => {
    const { user } = await openPage();
    for (const [name, dialog] of [
      ['Rename', 'Rename model'],
      ['Delete', 'Delete my-tsmixer.keras?'],
    ] as const) {
      const opener = () => screen.getByRole('button', { name });
      await user.click(opener());
      await screen.findByRole('dialog', { name: dialog });
      await user.keyboard('{Escape}');
      await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(document.activeElement).toBe(opener());
    }
  });
});
