import * as React from 'react';
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';

import { cn } from '@/lib/utils';

// shadcn/ui's DropdownMenu with the app design's menu (mockups/app, the account menu): 8 px
// corners, 4 px padding, 34 px items of 14 px text (its own line height) with 10 px around them and a muted icon, and a
// red variant for sign out. It pops in from 4 px above, growing out of its trigger's corner.

// Closes the menu around a content, for Tab to leave it (#468).
const CloseMenu = React.createContext<() => void>(() => {});

// Radix's root with its open state kept here, open or not, so that the content can close it.
function DropdownMenu({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Root>) {
  const [openState, setOpenState] = React.useState(defaultOpen);
  const open = openProp ?? openState;
  const setOpen = React.useCallback(
    (next: boolean) => {
      setOpenState(next);
      onOpenChange?.(next);
    },
    [onOpenChange],
  );
  const close = React.useCallback(() => setOpen(false), [setOpen]);
  return (
    <CloseMenu value={close}>
      <DropdownMenuPrimitive.Root data-slot="dropdown-menu" open={open} onOpenChange={setOpen} {...props} />
    </CloseMenu>
  );
}

function DropdownMenuTrigger({ ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Trigger>) {
  return <DropdownMenuPrimitive.Trigger data-slot="dropdown-menu-trigger" {...props} />;
}

// What the page reaches with Tab (tabindex 0 or more, enabled, shown), in the order of the page,
// without the focus guards Radix puts around the page while a menu is open.
function tabbables(): HTMLElement[] {
  const all = document.body.querySelectorAll<HTMLElement>(
    'a[href], button, input, select, textarea, summary, iframe, [tabindex], [contenteditable]',
  );
  return [...all].filter(
    (element) =>
      element.tabIndex >= 0 &&
      !element.matches(':disabled, input[type=hidden], [data-radix-focus-guard]') &&
      !element.closest('[hidden], [inert]') &&
      (element.checkVisibility?.({ visibilityProperty: true }) ?? true),
  );
}

type ContentProps = React.ComponentProps<typeof DropdownMenuPrimitive.Content>;

// Where Tab goes from the trigger of `menu` (Shift+Tab when `back`), the menu left out; null when
// nothing is there.
function besideTrigger(menu: HTMLElement, back: boolean): HTMLElement | null {
  const trigger = document.getElementById(menu.getAttribute('aria-labelledby') ?? '');
  const stops = tabbables().filter((element) => !menu.contains(element));
  const at = trigger ? stops.indexOf(trigger) : -1;
  return at === -1 ? null : (stops[at + (back ? -1 : 1)] ?? null);
}

// Tab leaves a menu as in the menu button pattern of WAI-ARIA: it closes the menu and moves the
// focus to what comes after the menu's trigger, Shift+Tab to what comes before it. Radix keeps
// the focus in the menu on Tab, so the content closes the menu itself and puts the focus there
// instead of on the trigger once it has closed. The trigger stays the stop when nothing is past it.
function useTabOut({ onKeyDown, onCloseAutoFocus }: Pick<ContentProps, 'onKeyDown' | 'onCloseAutoFocus'>) {
  const close = React.useContext(CloseMenu);
  const tabTo = React.useRef<HTMLElement | null>(null);
  return {
    onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => {
      onKeyDown?.(event);
      const tab = event.key === 'Tab' && !event.ctrlKey && !event.altKey && !event.metaKey;
      if (event.defaultPrevented || !tab) return;
      event.preventDefault();
      tabTo.current = besideTrigger(event.currentTarget, event.shiftKey);
      close();
    },
    onCloseAutoFocus: (event: Event) => {
      onCloseAutoFocus?.(event);
      const target = tabTo.current;
      tabTo.current = null;
      if (!target || event.defaultPrevented) return;
      event.preventDefault();
      target.focus();
    },
  };
}

function DropdownMenuContent({ className, sideOffset = 4, onKeyDown, onCloseAutoFocus, ...props }: ContentProps) {
  const tabOut = useTabOut({ onKeyDown, onCloseAutoFocus });
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        {...tabOut}
        className={cn(
          'z-50 flex max-h-(--radix-dropdown-menu-content-available-height) min-w-[8rem] origin-(--radix-dropdown-menu-content-transform-origin) flex-col overflow-x-hidden overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-[0_10px_28px_var(--shadow)]',
          'duration-140 ease-out data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-98 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-98 data-[side=bottom]:slide-in-from-top-1 data-[side=top]:slide-in-from-bottom-1 motion-reduce:animate-none',
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

function DropdownMenuLabel({ className, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Label>) {
  return (
    <DropdownMenuPrimitive.Label
      data-slot="dropdown-menu-label"
      className={cn('px-2.5 pt-2 pb-2.5', className)}
      {...props}
    />
  );
}

function DropdownMenuItem({
  className,
  variant = 'default',
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Item> & { variant?: 'default' | 'destructive' }) {
  return (
    <DropdownMenuPrimitive.Item
      data-slot="dropdown-menu-item"
      data-variant={variant}
      className={cn(
        "relative flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-sm/[normal] outline-hidden select-none focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[variant=destructive]:text-destructive data-[variant=destructive]:focus:bg-destructive/10 data-[variant=destructive]:focus:text-destructive [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground data-[variant=destructive]:*:[svg]:!text-destructive",
        className,
      )}
      {...props}
    />
  );
}

// One choice out of a few (the chips of Experiments): the items say which one is chosen.
function DropdownMenuRadioGroup({ ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.RadioGroup>) {
  return <DropdownMenuPrimitive.RadioGroup data-slot="dropdown-menu-radio-group" {...props} />;
}

function DropdownMenuRadioItem({ className, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.RadioItem>) {
  return (
    <DropdownMenuPrimitive.RadioItem
      data-slot="dropdown-menu-radio-item"
      className={cn(
        'group/item relative flex cursor-pointer items-center gap-2 rounded-sm outline-hidden select-none focus:bg-accent data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuSeparator({ className, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn('my-1 h-px shrink-0 bg-border', className)}
      {...props}
    />
  );
}

export {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
};
