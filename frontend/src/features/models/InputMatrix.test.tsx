// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Manifest } from '@/api';
import { catalogAnswer, manifest, stubApi } from '@/test/api';

import { InputMatrix } from './InputMatrix';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// The five prices of a candle, then a column the catalog has no group for: a group of one
// column, "Columns".
const BASE = manifest();
const SIX: Manifest = {
  ...BASE,
  input: {
    ...BASE.input,
    features: [...BASE.input.features, { name: 'spread', id: 'spread@1', norm: { id: 'none@1' } }],
  },
};

function renderMatrix() {
  stubApi(catalogAnswer);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InputMatrix manifest={SIX} />
    </QueryClientProvider>,
  );
}

const names = (container: HTMLElement) =>
  [...container.querySelectorAll<HTMLElement>('[data-group]')].map((name) => name.textContent);

describe('the input matrix (#465)', () => {
  it('gives a column 24 px and its gap on a phone, so a finger can pick it', async () => {
    renderMatrix();
    const toolbar = await screen.findByRole('toolbar', { name: 'Input columns' });
    expect(toolbar.parentElement?.className).toContain(
      'max-sm:grid-cols-[minmax(46px,auto)_minmax(calc(var(--columns)*26px),1fr)_auto]',
    );
    expect(toolbar.parentElement?.style.getPropertyValue('--columns')).toBe('6');
  });

  it('steps a group’s name up over its line, whole, when its columns have no room for it', async () => {
    // As a desktop lays out a column of 17 px: "Columns" is 44 px wide, "Candle" fits its five.
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.textContent === 'Columns' ? 44 : 38;
    });
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.textContent === 'Columns' ? 17 : 95;
    });
    const { container } = renderMatrix();
    // The groups come with the catalog.
    await vi.waitFor(() => expect(names(container)).toEqual(['Candle', 'Columns']));
    const raised = [...container.querySelectorAll('[data-raised]')];
    expect(raised.map((name) => name.textContent)).toEqual(['Columns']);
    expect(raised[0]?.className.split(' ')).toEqual(
      expect.arrayContaining(['absolute', 'bottom-full', 'whitespace-nowrap']),
    );
    const [candle, columns] = container.querySelectorAll('[data-group]');
    expect(columns?.className).toContain('invisible');
    expect(candle?.className).not.toContain('invisible');
  });

  it('leaves every name in its place when each fits', async () => {
    const { container } = renderMatrix();
    await vi.waitFor(() => expect(names(container)).toEqual(['Candle', 'Columns']));
    expect(container.querySelectorAll('[data-raised]')).toHaveLength(0);
  });
});
