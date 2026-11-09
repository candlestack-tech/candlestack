// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SortButton } from '@/features/backtests/table';

import { Slider } from './slider';
import { Switch } from './switch';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const classes = (element: Element) => element.className.split(' ');

// #465: the small controls take a tap of at least 24 × 24 px (WCAG 2.5.8) without growing.
describe('the targets of the small controls', () => {
  it('a switch takes 3 px around its 38 × 22 pill: 44 × 28', () => {
    render(<Switch aria-label="Reduce motion" />);
    expect(classes(screen.getByRole('switch', { name: 'Reduce motion' }))).toEqual(
      expect.arrayContaining(['relative', 'after:absolute', 'after:-inset-[3px]']),
    );
  });

  it('a slider’s thumb takes 4 px around its 16 px ring: 24 × 24', () => {
    // Radix measures the thumb; jsdom has no ResizeObserver.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    render(<Slider aria-label="Threshold" defaultValue={[5]} max={10} />);
    expect(classes(screen.getByRole('slider', { name: 'Threshold' }))).toEqual(
      expect.arrayContaining(['relative', 'size-4', 'border-2', 'after:absolute', 'after:-inset-1.5']),
    );
  });

  it('a header that sorts is 24 px high, its padding taking no room from the header', () => {
    render(
      <SortButton on={false} onClick={() => undefined}>
        Sharpe
      </SortButton>,
    );
    expect(classes(screen.getByRole('button', { name: 'Sharpe' }))).toEqual(
      expect.arrayContaining(['-my-0.5', 'py-0.5']),
    );
  });
});
