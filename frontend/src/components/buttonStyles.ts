// The mockups' buttons (.btn) as classes on shadcn's Button, for its variants to look like them.

// .btn: 34 px high with 14 px either side; .btn.lg, in the hero, 42 px with 20 px and 15 px text;
// .btn.sm, in the cards of Settings and on a model's tile, 28 px with 10 px, 13 px text and 6 px to
// its icon. Disabled, a button fades to 45 % and still shows its title on hover.
export const BUTTON_SIZES = {
  default: 'h-[34px] px-3.5',
  lg: 'h-[42px] px-5 text-[15px]',
  sm: 'h-7 gap-1.5 px-2.5 text-[13px] has-[>svg]:px-2.5 disabled:pointer-events-auto disabled:cursor-default disabled:opacity-45',
};

// .btn on the outline variant: no shadow, and a solid background with the full surface on hover
// in both themes, as the buttons stand on the grid.
export const OUTLINE_BUTTON = 'shadow-none dark:bg-background dark:hover:bg-accent';

// .btn.ghost on the ghost variant: a transparent 1 px border, so it lines up with the bordered
// ones, and the full surface on hover in dark too (the variant alone halves it there).
export const GHOST_BUTTON = 'border border-transparent dark:hover:bg-accent';

// .btn.pri on the default variant (Try Backtest, New backtest): the accent border makes the
// button 2 px wider and stays as a rim around the hover color. Disabled (Add model before the
// manifest is in), it drops the accent for the muted surface and text: faded, the accent still
// read as a button to press, in the dark theme above all.
export const ACCENT_BUTTON =
  'border border-primary shadow-none hover:bg-[color-mix(in_srgb,var(--accent)_86%,var(--ink))] disabled:border-border disabled:bg-surface-2 disabled:text-muted-foreground disabled:opacity-100';

// .btn.danger on the outline variant (Delete account): the text in the falling red, the border
// tinted with it.
export const DANGER_BUTTON =
  'border-[color-mix(in_srgb,var(--down)_45%,var(--rule))] text-down hover:text-down dark:border-[color-mix(in_srgb,var(--down)_45%,var(--rule))]';

// .btn.danger.pri on the default variant (the dialog's Delete account): filled with the red, which
// stays on hover.
export const DANGER_ACCENT_BUTTON = 'border border-down bg-down text-background shadow-none hover:bg-down';

// .fp-t, the dropdowns' button (the Instruments filter, the benchmark): 34 px with the text in the
// muted color, the full ink on hover and while open. It is the group of its chevron
// (DROPDOWN_CHEVRON), which turns while open. Each dropdown colors its border while open as its
// design does.
export const DROPDOWN_TRIGGER =
  'group flex h-[34px] items-center gap-2 rounded-md border bg-background pr-2.5 pl-3 text-left text-sm text-muted-foreground transition-[color,border-color,box-shadow] outline-none hover:text-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=open]:text-foreground';

// .fp-chev: lucide's ChevronDown at the end of a DROPDOWN_TRIGGER.
export const DROPDOWN_CHEVRON =
  'size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180';
