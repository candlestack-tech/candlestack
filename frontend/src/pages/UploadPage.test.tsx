// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ModelUpload } from '@/api';
import { ADA, MB, model, modelsAnswer, problem, session, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';
import { file } from '@/test/upload';
import { stubXhr } from '@/test/xhr';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TOKEN = 'q'.repeat(40);
const LINK = `GET /api/v1/uploads/${TOKEN}`;
const PAGE = `/upload/${TOKEN}`;
const minutes = (count: number) => new Date(Date.now() + count * 60_000).toISOString();

// The link of the design: made 2 minutes ago for a 31.4 MB Keras file, 13 minutes left.
const link = (fields: Partial<ModelUpload> = {}): ModelUpload => ({
  id: 'upl_7f3k2m9qx4',
  name: 'my-tsmixer.keras',
  size_bytes: Math.round(31.4 * MB),
  status: 'waiting',
  created_at: minutes(-2),
  expires_at: minutes(13),
  finished_at: null,
  model_id: null,
  problem: null,
  check: null,
  error: null,
  ...fields,
});

// 26.4 MB of the 100 MB taken, so 73.6 MB free.
const STORED = model({ id: 'mdl_oldoldoldo', name: 'old.keras', size_bytes: Math.round(26.4 * MB) });
const NEW = model({ id: 'mdl_newnewnewn', size_bytes: Math.round(31.4 * MB), input_shape: [64, 23] });

type Answer = Parameters<typeof stubApi>[0][string];

async function openPage(answer: Answer = { status: 200, body: link() }) {
  const sent = stubXhr();
  const fetch = stubApi({ ...session(ADA), ...modelsAnswer([STORED]), [LINK]: answer });
  const app = await renderApp(PAGE);
  await screen.findByRole('heading', { level: 1 });
  const choose = (item: File) => fireEvent.change(screen.getByLabelText('Model file'), { target: { files: [item] } });
  return { ...app, sent, fetch, choose, user: userEvent.setup() };
}

const RIGHT_FILE = () => file('my-tsmixer.keras', 31.4);

describe('UploadPage, ready', () => {
  it('shows the file the link expects, the drop zone, the time left and the storage it takes', async () => {
    await openPage();
    expect(screen.getByRole('heading', { level: 1, name: 'Upload your model' })).toBeTruthy();
    expect(document.title).toBe('Upload a model · CandleStack');
    expect(screen.getByText('Asked for 2 min ago')).toBeTruthy();
    expect(screen.getByText('my-tsmixer.keras')).toBeTruthy();
    expect(screen.getByText('31.4 MB · Keras')).toBeTruthy();
    expect(screen.getByText('Expected')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Drop my-tsmixer.keras here/ })).toBeTruthy();
    expect(screen.getByText('Link works for 13 more min')).toBeTruthy();
    expect(screen.getByText('31.4 MB of your 73.6 MB free')).toBeTruthy();
    expect(screen.getByLabelText<HTMLInputElement>('Model file').accept).toBe('.keras');
  });

  it('refuses a file of another size in the browser, so the link stays usable', async () => {
    const { sent, choose } = await openPage();
    choose(file('tsmixer-old.keras', 12));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Not the file your agent asked for');
    expect(alert.textContent).toContain('tsmixer-old.keras is 12.0 MB. Choose my-tsmixer.keras, or ask your agent');
    expect(sent).toHaveLength(0);
    expect(screen.getByRole('button', { name: /Drop my-tsmixer.keras here/ })).toBeTruthy();
  });

  it('names the bytes when the sizes read the same, and refuses another format', async () => {
    const { sent, choose } = await openPage();
    const almost = new File(['x'], 'my-tsmixer.keras');
    Object.defineProperty(almost, 'size', { value: Math.round(31.4 * MB) + 1 });
    choose(almost);
    expect((await screen.findByRole('alert')).textContent).toContain('is 32,925,287 bytes.');
    choose(file('my-tsmixer.onnx', 31.4));
    expect((await screen.findByRole('alert')).textContent).toContain('my-tsmixer.onnx is not a Keras file.');
    expect(sent).toHaveLength(0);
  });
});

describe('UploadPage, the upload', () => {
  it('sends the file to the link, shows its progress, the checks, then the new model', async () => {
    const { sent, choose } = await openPage();
    const item = RIGHT_FILE();
    choose(item);
    const [request] = sent;
    expect(request).toMatchObject({ method: 'PUT', url: `/api/v1/uploads/${TOKEN}`, body: item });
    expect(await screen.findByText('Keep this tab open until the upload ends.')).toBeTruthy();
    request?.progress(Math.round(14.4 * MB), item.size);
    expect(await screen.findByText('14.4 MB of 31.4 MB')).toBeTruthy();
    expect(screen.getByLabelText<HTMLProgressElement>('Upload').value).toBe(45);
    expect(screen.getByText('45 %')).toBeTruthy();
    expect(screen.getByText('Link works for 13 more min')).toBeTruthy();
    expect(screen.getByText('31.4 MB of your 73.6 MB free')).toBeTruthy();

    request?.progress(item.size, item.size);
    expect(await screen.findByText('Checks take a few seconds')).toBeTruthy();
    expect(screen.getByText(/Checking:/).parentElement?.textContent).toContain('Opens as a safe model');

    request?.respond(201, NEW);
    expect(await screen.findByRole('heading', { level: 1, name: 'Model uploaded' })).toBeTruthy();
    expect(screen.getByText('31.4 MB · Keras · input 64 × 23')).toBeTruthy();
    expect(screen.getByText('New')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open the model' }).getAttribute('href')).toBe('/models/mdl_newnewnewn');
    expect(screen.getByText('You can close this tab')).toBeTruthy();
    expect(screen.getByText('26.4 MB → 57.8 MB of 100 MB')).toBeTruthy();
    expect(screen.getAllByText(/^Passed:/)).toHaveLength(3);
  });

  it('shows the check that failed, and that the link is used up', async () => {
    const { sent, choose } = await openPage();
    choose(RIGHT_FILE());
    sent[0]?.respond(
      422,
      problem(422, 'model-check-failed', 'The file holds a Lambda layer with Python code.', { check: 'opens' }).body,
    );
    expect(await screen.findByRole('heading', { level: 1, name: 'The model did not pass the checks' })).toBeTruthy();
    expect(screen.getByText('Does not open as a safe model')).toBeTruthy();
    expect(screen.getByText('The file holds a Lambda layer with Python code.')).toBeTruthy();
    expect(screen.getByText('Not checked')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Which models work' }).getAttribute('href')).toBe(
      'https://candlestack.tech/docs/models',
    );
    expect(screen.getByText('This link is used up')).toBeTruthy();
    expect(screen.getByText('Nothing taken from your storage')).toBeTruthy();
    expect(screen.getByText(/Fix the file, then ask your agent/)).toBeTruthy();
  });

  it('says what else stopped the upload', async () => {
    const { sent, choose } = await openPage();
    choose(RIGHT_FILE());
    sent[0]?.respond(408, problem(408, 'upload-timeout', 'The file did not arrive within 2 minutes.').body);
    expect(await screen.findByRole('heading', { level: 1, name: 'The upload did not finish' })).toBeTruthy();
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('Upload timed out');
    expect(alert.textContent).toContain('The file did not arrive within 2 minutes.');
    expect(screen.getByText('Nothing was added to your account. Ask your agent for a new link.')).toBeTruthy();
  });

  it('goes back to the drop zone when the API turned the upload away before using the link', async () => {
    const { sent, choose } = await openPage();
    choose(RIGHT_FILE());
    sent[0]?.respond(409, problem(409, 'upload-in-progress', 'Another upload of yours is running.').body);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Another upload is in progress');
    expect(alert.textContent).toContain('Another upload of yours is running.');
    expect(screen.getByRole('button', { name: /Drop my-tsmixer.keras here/ })).toBeTruthy();
  });

  it('cancels the upload, which uses the link up', async () => {
    const { sent, choose, user } = await openPage();
    choose(RIGHT_FILE());
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(sent[0]?.aborted).toBe(true);
    expect(await screen.findByRole('heading', { level: 1, name: 'Upload cancelled' })).toBeTruthy();
    expect(screen.getByText(/so this one is used up/)).toBeTruthy();
  });

  it('asks before the tab closes mid-upload, and no longer once it ended', async () => {
    const { sent, choose } = await openPage();
    choose(RIGHT_FILE());
    await screen.findByText('Keep this tab open until the upload ends.');
    const leave = () => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(leave()).toBe(true);
    sent[0]?.respond(201, NEW);
    await screen.findByRole('heading', { level: 1, name: 'Model uploaded' });
    expect(leave()).toBe(false);
  });

  it('shows the model when a cancel came after the whole file had gone out', async () => {
    let answer = link();
    const { sent, choose, user } = await openPage(() => Promise.resolve({ status: 200, body: answer }));
    const item = RIGHT_FILE();
    choose(item);
    sent[0]?.progress(item.size - 1, item.size);
    answer = link({ status: 'done', model_id: 'mdl_newnewnewn', finished_at: minutes(0) });
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'This link was already used' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open the model' }).getAttribute('href')).toBe('/models/mdl_newnewnewn');
  });

  it('shows the link as it now stands when it was used meanwhile', async () => {
    let answer = link();
    const { sent, choose } = await openPage(() => Promise.resolve({ status: 200, body: answer }));
    choose(RIGHT_FILE());
    answer = link({ status: 'done', model_id: 'mdl_otherother', finished_at: minutes(0) });
    sent[0]?.respond(410, problem(410, 'upload-link-gone', 'This upload link took its file already.').body);
    expect(await screen.findByRole('heading', { level: 1, name: 'This link was already used' })).toBeTruthy();
  });
});

describe('UploadPage, the link cannot take a file', () => {
  it('says that an expired link has expired, also one that expires while the page is open', async () => {
    await openPage({ status: 200, body: link({ status: 'expired', expires_at: minutes(-1) }) });
    expect(screen.getByRole('heading', { level: 1, name: 'This link has expired' })).toBeTruthy();
    expect(screen.getByText(/Links work for 15 minutes/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Go to Models' }).getAttribute('href')).toBe('/models');
    cleanup();
    await openPage({ status: 200, body: link({ expires_at: minutes(-0.1) }) });
    expect(screen.getByRole('heading', { level: 1, name: 'This link has expired' })).toBeTruthy();
  });

  it('says that a used link took its file, with the model it made', async () => {
    await openPage({
      status: 200,
      body: link({ status: 'done', model_id: 'mdl_newnewnewn', finished_at: minutes(-4) }),
    });
    expect(screen.getByRole('heading', { level: 1, name: 'This link was already used' })).toBeTruthy();
    expect(screen.getByText(/Each link takes one file\./).textContent).toBe(
      'Each link takes one file. my-tsmixer.keras came through it 4 min ago.',
    );
    expect(screen.getByRole('link', { name: 'Open the model' }).getAttribute('href')).toBe('/models/mdl_newnewnewn');
    expect(screen.getByRole('link', { name: 'Go to Models' })).toBeTruthy();
  });

  it('shows a link that failed before the page opened', async () => {
    await openPage({
      status: 200,
      body: link({ status: 'failed', problem: 'model-check-failed', check: 'weights', error: 'No weights.' }),
    });
    expect(screen.getByRole('heading', { level: 1, name: 'The model did not pass the checks' })).toBeTruthy();
    expect(screen.getByText('No trained weights in the file')).toBeTruthy();
    expect(screen.getByText('No weights.')).toBeTruthy();
  });

  it('shows the checks of an upload running in another tab', async () => {
    await openPage({ status: 200, body: link({ status: 'checking' }) });
    expect(screen.getByText('Checks take a few seconds')).toBeTruthy();
    expect(screen.getByText(/Checking:/).parentElement?.textContent).toContain('Upload in progress in another tab');
    expect(screen.queryByText(/^Passed:/)).toBeNull();
  });

  it('says a link that stopped working while the page was open does not work', async () => {
    let answer: { status: number; body?: unknown } = { status: 200, body: link() };
    const { queryClient } = await openPage(() => Promise.resolve(answer));
    answer = problem(404, 'upload-link-not-found', 'No upload link has this token.');
    await queryClient.refetchQueries();
    expect(await screen.findByRole('heading', { level: 1, name: 'This link does not work' })).toBeTruthy();
  });

  it('says a link of another account is not for this one, and switches accounts', async () => {
    const { router, user } = await openPage(
      problem(403, 'upload-link-of-another-user', 'Another account asked for this upload link.'),
    );
    expect(screen.getByRole('heading', { level: 1, name: 'This link is for another account' })).toBeTruthy();
    expect(screen.getByText('ada@example.com')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Go to Dashboard' }).getAttribute('href')).toBe('/dashboard');
    const signOut = stubApi({ ...session(null), 'POST /api/v1/auth/sign-out': { status: 204 } });
    await user.click(screen.getByRole('button', { name: 'Use another account' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/signin'));
    expect(router.state.location.search).toEqual({ next: PAGE });
    expect(signOut).toHaveBeenCalledWith('/api/v1/auth/sign-out', expect.objectContaining({ method: 'POST' }));
  });

  it('says an unknown link does not work', async () => {
    await openPage(problem(404, 'upload-link-not-found', 'No upload link has this token.'));
    expect(screen.getByRole('heading', { level: 1, name: 'This link does not work' })).toBeTruthy();
    expect(screen.getByText(/Check that the whole link was copied/)).toBeTruthy();
  });

  it('offers to try again when the link could not be read', async () => {
    const { user } = await openPage(problem(500, 'internal-error', 'Something broke on our side.'));
    expect(screen.getByRole('heading', { level: 1, name: 'Could not load this link' })).toBeTruthy();
    stubApi({ ...session(ADA), ...modelsAnswer([STORED]), [LINK]: { status: 200, body: link() } });
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Upload your model' })).toBeTruthy();
  });

  it('sends a signed-out visitor to sign in, and back here after', async () => {
    stubApi(session(null));
    const { router } = await renderApp(PAGE);
    expect(router.state.location.pathname).toBe('/signin');
    expect(router.state.location.search).toEqual({ next: PAGE });
    expect(within(document.body).queryByText('Upload your model')).toBeNull();
  });
});
