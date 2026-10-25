import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

// The public pages leave 32 px under the title; the signed-in pages (mockups/app, .ttl) 20 px,
// with the line under the title 4 px closer.
const VARIANTS = {
  public: { row: 'mb-8', description: 'mt-2' },
  workspace: { row: 'mb-5', description: 'mt-1' },
};

type Props = {
  title: string;
  // A line of text; the Models page colours a part of it.
  description?: ReactNode;
  variant?: keyof typeof VARIANTS;
  // What the page offers beside its title, e.g. the Models page's Upload card. Bottom-aligned at
  // the right, under the title when the row is too narrow for both.
  action?: ReactNode;
};

// A page's title row: 30/36 semibold, a muted line under it; with an action, the two side by side.
export function PageTitle({ title, description, variant = 'public', action }: Props) {
  const spacing = VARIANTS[variant];
  const heading = (
    <>
      <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
      {/* break-words: an error's description can hold a long path or id. */}
      {description && <p className={cn(spacing.description, 'break-words text-muted-foreground')}>{description}</p>}
    </>
  );
  if (!action) return <div className={spacing.row}>{heading}</div>;
  return (
    <div className={cn(spacing.row, 'flex flex-wrap items-end justify-between gap-3')}>
      <div>{heading}</div>
      {action}
    </div>
  );
}
