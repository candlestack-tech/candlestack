import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';

// The full name of a name cut with an ellipsis (backtest-names, .ntip): ink on the page's colour,
// 8 px under the name at `at` (useNameTooltip), at most 360 px wide, its arrow under the start of
// the name, kept 8 px inside the window.

const EDGE = 8;

export function NameTooltip({ text, at }: { text: string; at: DOMRect }) {
  const tip = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ left: at.left, arrow: 14 });
  useLayoutEffect(() => {
    const width = tip.current?.offsetWidth ?? 0;
    const left = Math.max(EDGE, Math.min(at.left, window.innerWidth - width - EDGE));
    setPlace({ left, arrow: Math.max(10, Math.min(width - 18, at.left + 14 - left)) });
  }, [at]);
  const { left, arrow } = place;
  return createPortal(
    <div
      ref={tip}
      role="tooltip"
      style={{ left, top: at.bottom + 8, '--ax': `${String(arrow)}px` } as CSSProperties}
      className="pointer-events-none fixed z-[90] max-w-[360px] rounded-md bg-foreground px-2.5 py-[7px] text-[12.5px]/[1.4] font-medium whitespace-normal text-background shadow-[0_8px_24px_var(--shadow)] [overflow-wrap:anywhere] before:absolute before:-top-1 before:left-(--ax) before:size-2 before:rotate-45 before:bg-foreground"
    >
      {text}
    </div>,
    document.body,
  );
}
