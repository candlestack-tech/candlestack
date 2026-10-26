// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SaveState } from './autosave';
import { SaveIndicator } from './SaveIndicator';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const state = (status: SaveState['status'], invalid = false): SaveState => ({ status, invalid, retry: () => {} });
const shown = () => screen.getByRole('status').textContent;
const wait = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

describe('the save indicator', () => {
  it('says Saving… only when a save takes over 300 ms', () => {
    const { rerender } = render(<SaveIndicator state={state('pending')} />);
    expect(shown()).toBe('');
    wait(299);
    expect(shown()).toBe('');
    wait(1);
    expect(shown()).toBe('Saving…');
    rerender(<SaveIndicator state={state('success')} />);
    expect(shown()).toBe('Saved');
  });

  it('says Saved, fades it out after 2 s, and says it again after the next save', () => {
    const { rerender } = render(<SaveIndicator state={state('pending')} />);
    rerender(<SaveIndicator state={state('success')} />);
    expect(screen.getByText('Saved').className).not.toContain('opacity-0');
    wait(2000);
    expect(screen.getByText('Saved').className).toContain('opacity-0');
    wait(300);
    expect(shown()).toBe('');

    rerender(<SaveIndicator state={state('pending')} />);
    rerender(<SaveIndicator state={state('success')} />);
    expect(shown()).toBe('Saved');
  });

  it('says Not saved while a field is to fix, over any save', () => {
    render(<SaveIndicator state={state('success', true)} />);
    expect(shown()).toBe('Not saved');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it("says Couldn't save with Retry, which sends again", () => {
    const retry = vi.fn();
    render(<SaveIndicator state={{ status: 'error', retry }} />);
    expect(shown()).toBe("Couldn't save");
    act(() => screen.getByRole('button', { name: 'Retry' }).click());
    expect(retry).toHaveBeenCalledOnce();
  });

  it('sits centred on the title row, so the card does not move as it shows and hides', () => {
    // The card's title row lines its parts up on their text (DashboardCard); with nothing to say,
    // the indicator has no text and would sit lower, making the row 6.5 px taller until it speaks.
    render(<SaveIndicator state={state('idle')} />);
    expect(screen.getByRole('status').parentElement?.className).toMatch(/(^| )self-center( |$)/);
  });
});
