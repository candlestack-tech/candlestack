import { XIcon } from 'lucide-react';
import type { ComponentType, ReactNode, SVGProps } from 'react';

import { HINT } from '@/components/Hint';
import { cn } from '@/lib/utils';

import { FrameworkTile } from './FrameworkMark';
import type { Framework } from './frameworks';

// The parts of the upload page of a one-time link (design/mockups/api-mcp, pageUpload): the card
// in the middle, the file row, the progress bar, the line under the card and the short state cards.

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

const CARD =
  'flex flex-col gap-[18px] rounded-xl border bg-background px-7 pt-7 pb-6 shadow-[0_10px_30px_var(--shadow)] animate-in duration-220 ease-out fade-in slide-in-from-bottom-2 motion-reduce:animate-none max-[640px]:px-[18px] max-[640px]:pt-6 max-[640px]:pb-5';
const TITLE = 'text-[22px]/7 font-semibold tracking-[-0.02em]';
const INTRO = 'mt-1.5 text-muted-foreground [&_b]:font-medium [&_b]:text-foreground';

// The page around the card: in the middle of what the header and the footer leave.
export function UploadPageFrame({ children }: { children: ReactNode }) {
  return (
    <section
      aria-label="Upload a model"
      className="flex min-h-[calc(100vh-56px-130px)] flex-col items-center justify-center px-4 py-12 max-[640px]:justify-start max-[640px]:pt-7"
    >
      {children}
    </section>
  );
}

type CardProps = { top?: ReactNode; title: string; intro: ReactNode; children: ReactNode };

// .one.ul: who asked, the title and the intro over what the page shows now.
export function LinkCard({ top, title, intro, children }: CardProps) {
  return (
    <div className={cn(CARD, 'w-[min(480px,100%)]')}>
      <div className="flex flex-col gap-1.5">
        {top}
        <div>
          <h1 className={TITLE}>{title}</h1>
          <p className={INTRO}>{intro}</p>
        </div>
      </div>
      {children}
    </div>
  );
}

const TONES = {
  plain: 'bg-surface-2 text-muted-foreground',
  warn: 'bg-[color-mix(in_srgb,var(--warn)_11%,var(--bg))] text-warn',
  down: 'bg-[color-mix(in_srgb,var(--down)_11%,var(--bg))] text-down',
};

type StateProps = { icon: Icon; tone?: keyof typeof TONES; title: string; intro: ReactNode; actions: ReactNode };

// .one.mid: a link that cannot take a file, as a short card with its icon and one way on.
export function StateCard({ icon: StateIcon, tone = 'plain', title, intro, actions }: StateProps) {
  return (
    <div className={cn(CARD, 'w-[min(440px,100%)] items-center text-center')}>
      <span className={cn('grid size-11 place-items-center rounded-xl', TONES[tone])}>
        <StateIcon aria-hidden="true" className="size-[21px]" />
      </span>
      <div>
        <h1 className={TITLE}>{title}</h1>
        <p className={INTRO}>{intro}</p>
      </div>
      <div className="flex flex-wrap justify-center gap-2.5">{actions}</div>
    </div>
  );
}

// .ul-from: who asked for the link, and when.
export function AskedFor({ icon: AskIcon, children }: { icon: Icon; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-[7px] text-[12.5px] text-muted-foreground">
      <span className="grid size-[18px] place-items-center rounded-[5px] bg-surface-2 text-foreground">
        <AskIcon aria-hidden="true" className="size-3" />
      </span>
      {children}
    </span>
  );
}

// .tag: Expected, New.
export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-[4px] border px-1.5 py-px font-mono text-[11px]/4 font-medium whitespace-nowrap text-muted-foreground">
      {children}
    </span>
  );
}

type FileProps = { name: string; framework: Framework | null; line: string; failed?: boolean; children?: ReactNode };

// .file: the framework's tile, the file's name in mono and what is known of it, and on the right
// a tag or Cancel.
export function FileRow({ name, framework, line, failed = false, children }: FileProps) {
  return (
    <div
      className={cn(
        'grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-lg border px-3 py-2.5',
        failed && 'border-[color-mix(in_srgb,var(--down)_45%,var(--rule))]',
      )}
    >
      {framework ? <FrameworkTile framework={framework} size="lg" /> : <span className="size-9" />}
      <span className="flex min-w-0 flex-col">
        <b className="truncate font-mono text-[13.5px] font-medium">{name}</b>
        <span className="text-[12px] text-muted-foreground">{line}</span>
      </span>
      <span className="flex items-center gap-1.5">{children}</span>
    </div>
  );
}

// .note.err: what is wrong with the file chosen, on a red wash. Read out as soon as it shows.
export function ErrorBox({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-[9px] rounded-lg border border-[color-mix(in_srgb,var(--down)_35%,var(--rule))] bg-[color-mix(in_srgb,var(--down)_11%,var(--background))] px-3 py-2.5 text-[13px]"
    >
      <XIcon aria-hidden="true" className="mt-0.5 size-[15px] flex-none text-down" />
      <span className="flex min-w-0 flex-col gap-0.5 break-words">
        <b className="font-semibold">{title}</b>
        <span>{children}</span>
      </span>
    </div>
  );
}

type ProgressProps = { sent: number; total: number; sentText: string };

// .ul-p: the bar and, under it, how much is sent and the percentage.
export function UploadProgress({ sent, total, sentText }: ProgressProps) {
  const percent = total > 0 ? Math.min(100, Math.floor((sent / total) * 100)) : 0;
  return (
    <div className="flex flex-col gap-1.5">
      <progress
        aria-label="Upload"
        max={100}
        value={percent}
        className="h-1.5 w-full appearance-none overflow-hidden rounded-[3px] bg-surface-2 [&::-moz-progress-bar]:rounded-[3px] [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:bg-surface-2 [&::-webkit-progress-value]:rounded-[3px] [&::-webkit-progress-value]:bg-primary [&::-webkit-progress-value]:transition-[width]"
      />
      <div className="flex justify-between gap-2.5 font-mono text-[12px] text-muted-foreground">
        <span>{sentText}</span>
        <span>{percent} %</span>
      </div>
    </div>
  );
}

type MetaProps = { left: ReactNode; right?: ReactNode };

// .ul-meta: the line under a rule at the foot of the card (the time left, the storage).
export function MetaLine({ left, right }: MetaProps) {
  return (
    <div className="flex flex-wrap justify-between gap-2.5 border-t pt-3 text-[12px] text-muted-foreground [&_svg]:size-[13px]">
      <span className="inline-flex items-center gap-1.5">{left}</span>
      {right && <span className="inline-flex items-center gap-1.5">{right}</span>}
    </div>
  );
}

export const CardHint = ({ children }: { children: ReactNode }) => <p className={HINT}>{children}</p>;
