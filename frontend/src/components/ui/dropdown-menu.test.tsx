// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './dropdown-menu';

// Radix measures the trigger and captures the pointer through browser APIs jsdom lacks.
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Object.assign(Element.prototype, { hasPointerCapture: () => false, releasePointerCapture: () => undefined });
});

afterEach(cleanup);

type MenuProps = { modal?: boolean; last?: boolean; onSelect?: () => void; onOpenChange?: (open: boolean) => void };

// A menu between two buttons (none after it when `last`), its open state kept by the page when
// `onOpenChange` is given.
function Page({ modal, last = false, onSelect, onOpenChange }: MenuProps) {
  const [open, setOpen] = useState(false);
  const controlled = onOpenChange && {
    open,
    onOpenChange: (next: boolean) => {
      setOpen(next);
      onOpenChange(next);
    },
  };
  return (
    <>
      <button type="button">Before</button>
      <DropdownMenu modal={modal} {...controlled}>
        <DropdownMenuTrigger>Sort</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={onSelect}>Newest</DropdownMenuItem>
          <DropdownMenuItem>Oldest</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {!last && <button type="button">After</button>}
    </>
  );
}

// Opens the menu from the keyboard, as a keyboard user does: the first item gets the focus.
async function openMenu(props: MenuProps = {}) {
  render(<Page {...props} />);
  const user = userEvent.setup();
  screen.getByRole('button', { name: 'Sort' }).focus();
  await user.keyboard('{Enter}');
  await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Newest' })));
  return user;
}

const focused = () => document.activeElement?.textContent;

describe('DropdownMenu', () => {
  for (const modal of [true, false]) {
    describe(modal ? 'modal' : 'not modal', () => {
      it('closes on Tab and moves the focus to what comes after its trigger', async () => {
        const user = await openMenu({ modal });
        await user.tab();
        await vi.waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
        expect(focused()).toBe('After');
      });

      it('closes on Shift+Tab and moves the focus to what comes before its trigger', async () => {
        const user = await openMenu({ modal });
        await user.tab({ shift: true });
        await vi.waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
        expect(focused()).toBe('Before');
      });
    });
  }

  it('leaves the focus on its trigger when nothing comes after it', async () => {
    const user = await openMenu({ last: true });
    await user.tab();
    await vi.waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(focused()).toBe('Sort');
  });

  it('tells a page that keeps its open state that Tab closed it', async () => {
    const onOpenChange = vi.fn();
    const user = await openMenu({ onOpenChange });
    await user.tab();
    await vi.waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(onOpenChange.mock.calls).toEqual([[true], [false]]);
    expect(focused()).toBe('After');
  });

  it('still moves with the arrows, picks with Enter and closes on Escape', async () => {
    const onSelect = vi.fn();
    const user = await openMenu({ onSelect });
    await user.keyboard('{ArrowDown}');
    expect(focused()).toBe('Oldest');
    await user.keyboard('{ArrowUp}');
    expect(focused()).toBe('Newest');
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledOnce();
    await vi.waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(focused()).toBe('Sort');

    await user.keyboard('{Enter}');
    await screen.findByRole('menu');
    await user.keyboard('{Escape}');
    await vi.waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    expect(focused()).toBe('Sort');
  });
});
