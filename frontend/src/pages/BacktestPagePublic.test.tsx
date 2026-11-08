// @vitest-environment jsdom
import { cleanup, screen, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BacktestDocument } from '@/api';
import { clearCompare } from '@/features/backtests/compare/selection';
import { ARSEN, backtest, backtestDocument, problem, sentBody, sessionWithPreferences, stubApi } from '@/test/api';
import { runningDocument } from '@/test/runs';
import { renderApp, viewerIn } from '@/test/app';
import { candles } from '@/test/candles';
import { othersDocument } from '@/test/experiments';
import { charts } from '@/test/lightweightCharts';

vi.mock('lightweight-charts', () => import('@/test/lightweightCharts'));

// Publishing a backtest from its page, and another account's published one, read only
// (public-experiments: pageBt, MODALS.publish and .unpublish, authorCard).

const ID = 'bt_7f3k2m9qx4';
const OTHERS = 'bt_t9n8v7d6a5';
const CANDLES = 'GET /api/v1/data/instruments/stock%3AAAPL/candles?timeframe=30m&start=1717372800&end=1717459200';
const TIMES = backtestDocument().results!.series.columns.ts;
const PUBLISHED_AT = '2026-10-17T14:41:00Z';

const published = (): BacktestDocument => {
  const doc = backtestDocument();
  return { ...doc, backtest: { ...doc.backtest, public: true, published_at: PUBLISHED_AT } };
};

type Answers = Parameters<typeof stubApi>[0];

async function open(id: string, answers: Answers) {
  const fetch = stubApi({
    ...sessionWithPreferences(ARSEN),
    [CANDLES]: { status: 200, body: candles(TIMES) },
    ...answers,
  });
  await renderApp(`/backtests/${id}`);
  await screen.findByRole('heading', { level: 1 });
  return { fetch, user: userEvent.setup() };
}

beforeEach(() => {
  clearCompare();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-17T16:00:00Z'));
  viewerIn('Europe/Bratislava');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  charts.reset();
});

const header = () => screen.getByRole('heading', { level: 1 }).parentElement!.parentElement!;
const buttons = () =>
  within(header())
    .getAllByRole('button')
    .map((button) => button.textContent);

describe('publishing a backtest', () => {
  it('offers Publish between Download JSON and Add to compare', async () => {
    await open(ID, { [`GET /api/v1/backtests/${ID}`]: { status: 200, body: backtestDocument() } });
    expect(buttons()).toEqual(['Rename', 'Download JSON', 'Publish', 'Add to compare', 'Delete']);
    expect(screen.queryByText('Public')).toBeNull();
  });

  it('shows the card it gets in Experiments and what others see, then publishes it', async () => {
    let doc = backtestDocument();
    const { user, fetch } = await open(ID, {
      [`GET /api/v1/backtests/${ID}`]: async () => Promise.resolve({ status: 200, body: doc }),
      [`PATCH /api/v1/backtests/${ID}`]: async () => {
        doc = published();
        return Promise.resolve({ status: 200, body: backtest({ public: true, published_at: PUBLISHED_AT }) });
      },
    });
    await user.click(within(header()).getByRole('button', { name: 'Publish' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Publish Bright Vega?' }));
    expect(
      dialog.getByText('Everyone on the CandleStack beta can see it. It never goes on the public web.'),
    ).toBeDefined();
    const card = within(dialog.getByRole('article', { name: 'Your card as others will see it' }));
    expect(card.queryByRole('link')).toBeNull();
    expect(card.queryByRole('button')).toBeNull();
    // The card names it on two lines, without its number (#376).
    expect(
      ['Bright Vega', 'AAPL · Candles · 30m', 'in 25 × 5', 'Buy and hold −2.0 %', 'Arsen Labovich', 'Today'].every(
        (text) => card.getByText(text),
      ),
    ).toBe(true);
    expect(card.queryByText('#12')).toBeNull();
    const lists = dialog.getAllByRole('list').map((list) =>
      within(list)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    );
    expect(lists).toEqual([
      [
        'Its nameBright Vega · you can rename it first',
        'Results, charts and the 1 trade',
        'The setupData, decision, fees, benchmark',
        'The model’s name and what it readsraw-candles.keras · Candles · 30m · 25 × 5',
        'Your name and initialsALArsen Labovich',
      ],
      ['The model fileNo download, no copy, no Run again', 'Your email', 'Your other backtests and models'],
    ]);
    expect(dialog.getByText('You can unpublish it any time')).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Publish' }));
    expect(sentBody(fetch, `PATCH /api/v1/backtests/${ID}`)).toEqual({ public: true });
    expect((await screen.findByRole('status')).textContent).toBe('Bright Vega is in Experiments now');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(within(header()).getByText('Public').getAttribute('title')).toBe(
      'Everyone on the CandleStack beta can see it',
    );
    expect(screen.getByText('In Experiments since today 16:41')).toBeDefined();
    expect(buttons()).toEqual(['Rename', 'Download JSON', 'Copy link', 'Unpublish', 'Add to compare', 'Delete']);
  });

  it('says why the API refused, and stays open', async () => {
    const { user } = await open(ID, {
      [`GET /api/v1/backtests/${ID}`]: { status: 200, body: backtestDocument() },
      [`PATCH /api/v1/backtests/${ID}`]: problem(
        409,
        'no-document',
        'This backtest is test data without a result document.',
      ),
    });
    await user.click(within(header()).getByRole('button', { name: 'Publish' }));
    const dialog = within(await screen.findByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Publish' }));
    expect((await dialog.findByRole('alert')).textContent).toBe(
      'This backtest is test data without a result document.',
    );
  });

  it('copies the link that opens for the beta only', async () => {
    const { user } = await open(ID, { [`GET /api/v1/backtests/${ID}`]: { status: 200, body: published() } });
    await user.click(screen.getByRole('button', { name: 'Copy link' }));
    expect(await navigator.clipboard.readText()).toBe(`${window.location.origin}/backtests/${ID}`);
    expect((await screen.findByRole('status')).textContent).toBe(
      'Link copied. It opens only for people on the CandleStack beta.',
    );
  });

  it('unpublishes it after saying it also leaves the Compare of others', async () => {
    let doc = published();
    const { user, fetch } = await open(ID, {
      [`GET /api/v1/backtests/${ID}`]: async () => Promise.resolve({ status: 200, body: doc }),
      [`PATCH /api/v1/backtests/${ID}`]: async () => {
        doc = backtestDocument();
        return Promise.resolve({ status: 200, body: backtest() });
      },
    });
    await user.click(screen.getByRole('button', { name: 'Unpublish' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Unpublish Bright Vega?' }));
    expect(dialog.getByText('Bright Vega')).toBeDefined();
    expect(dialog.getByText('AAPL · Candles · 30m')).toBeDefined();
    expect(
      dialog.getByText(
        'It leaves Experiments now. It also leaves the Compare of anyone who added it, and their panel says it was unpublished.',
      ),
    ).toBeDefined();
    expect(dialog.getByText('The backtest stays in your Backtests. You can publish it again later.')).toBeDefined();
    await user.click(dialog.getByRole('button', { name: 'Unpublish' }));
    expect(sentBody(fetch, `PATCH /api/v1/backtests/${ID}`)).toEqual({ public: false });
    expect((await screen.findByRole('status')).textContent).toBe('Bright Vega left Experiments');
    expect(within(header()).getByRole('button', { name: 'Publish' })).toBeDefined();
    expect(screen.queryByText(/In Experiments since/)).toBeNull();
  });

  it('keeps Publish off with the reason while it runs or once it failed', async () => {
    await open(ID, { [`GET /api/v1/backtests/${ID}`]: { status: 200, body: runningDocument() } });
    const running = within(header()).getByRole('button', { name: 'Publish' });
    expect(running.getAttribute('aria-disabled')).toBe('true');
    expect(document.getElementById(running.getAttribute('aria-describedby')!)?.textContent).toBe(
      'You can publish it once it finishes.',
    );
    cleanup();

    const doc = runningDocument([]);
    await open(ID, {
      [`GET /api/v1/backtests/${ID}`]: {
        status: 200,
        body: { ...doc, backtest: { ...doc.backtest, status: 'failed', error: 'No prediction.' } },
      },
    });
    const failed = within(header()).getByRole('button', { name: 'Publish' });
    expect(screen.getByRole('tooltip').textContent).toBe(
      'A failed backtest has no results to publish. Only finished ones can go to Experiments.',
    );
    expect(failed.getAttribute('aria-describedby')).toBe(screen.getByRole('tooltip').id);
  });
});

describe("another account's published backtest", () => {
  it('is the same page, read only: no Rename and no Publish, its author first under the title', async () => {
    await open(OTHERS, { [`GET /api/v1/backtests/${OTHERS}`]: { status: 200, body: othersDocument() } });
    // Its name, the number in its author's account beside it (#376).
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Velvet Deneb');
    expect(document.title).toBe('Velvet Deneb · CandleStack');
    expect(within(header()).getByText('#9').getAttribute('title')).toBe('Backtest number 9 in Tymur’s account');
    expect(buttons()).toEqual(['Download JSON', 'Add to compare']);
    expect(screen.getByRole('button', { name: 'Download JSON' }).getAttribute('title')).toBe(
      'Results, trades and setup. No model file.',
    );
    const line = header().nextElementSibling!;
    expect(line.firstElementChild?.textContent).toBe('TTTymur Tkach');
    expect(within(line as HTMLElement).getByText('Published today')).toBeDefined();
    // The model by name, never a link: the file stays with its author.
    expect(screen.queryByRole('link', { name: 'raw-candles.keras' })).toBeNull();
    expect(screen.getByText('The model file stays with Tymur. You see what it reads, not the file.')).toBeDefined();
    expect(screen.getByRole('link', { name: '← Experiments' }).getAttribute('href')).toBe('/experiments');
  });

  it('names its author on top of the right column, with the rest of theirs a click away', async () => {
    await open(OTHERS, { [`GET /api/v1/backtests/${OTHERS}`]: { status: 200, body: othersDocument() } });
    const author = within(screen.getByRole('region', { name: 'Author' }));
    expect(author.getByText('Tymur Tkach')).toBeDefined();
    expect(author.getByText('Published 17 Oct 2026 at 12:14')).toBeDefined();
    expect(author.getByRole('link', { name: 'More by Tymur →' }).getAttribute('href')).toBe(
      '/experiments?author=usr_t9m8r7k6j5',
    );
    const aside = screen.getByRole('region', { name: 'Author' }).parentElement!;
    expect(aside.firstElementChild?.getAttribute('aria-label')).toBe('Author');
  });

  it('goes to compare with its author', async () => {
    const { user } = await open(OTHERS, {
      [`GET /api/v1/backtests/${OTHERS}`]: { status: 200, body: othersDocument() },
    });
    await user.click(screen.getByRole('button', { name: 'Add to compare' }));
    expect(within(screen.getByRole('complementary', { name: 'Compare' })).getByTitle('Tymur Tkach')).toBeDefined();
  });

  it('says it is not found once its author unpublished it', async () => {
    await open(OTHERS, { [`GET /api/v1/backtests/${OTHERS}`]: problem(404, 'not-found', 'Not found.') });
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Not found');
  });
});
