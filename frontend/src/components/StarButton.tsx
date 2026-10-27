import { cn } from '@/lib/utils';

type Props = {
  starred: boolean;
  // What it stars, for screen readers: "Star model lstm.keras".
  label: string;
  onToggle: () => void;
  className?: string;
};

// The star of a model or a backtest (mockups/app, .star): ☆ or ★ in a 28 px target that grows a
// little on hover and gives on a press. A set star fades on hover, so it reads as "click to
// remove". Nothing else moves when it toggles.
export function StarButton({ starred, label, onToggle, className }: Props) {
  return (
    <button
      type="button"
      title={starred ? 'Remove star' : 'Star'}
      aria-label={label}
      aria-pressed={starred}
      onClick={onToggle}
      className={cn(
        '-mx-0.5 -my-1 inline-grid size-7 flex-none cursor-pointer place-items-center rounded-md text-[19px] leading-none text-control-off transition-[background-color,color,scale] duration-150 outline-none hover:scale-112 hover:bg-muted hover:text-warn focus-visible:ring-[3px] focus-visible:ring-ring/50 active:scale-92 aria-pressed:text-warn aria-pressed:hover:text-[color-mix(in_srgb,var(--warn)_45%,var(--bg))] motion-reduce:transition-none',
        className,
      )}
    >
      {starred ? '★' : '☆'}
    </button>
  );
}
