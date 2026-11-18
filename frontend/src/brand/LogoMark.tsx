// The tile alone (the design repository's brand/icon-dark.svg): black candles on the light teal
// tile in both themes, like the favicon. Decorative: the page names CandleStack in text.
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={className}>
      <rect fill="#3cc7a6" width="32" height="32" rx="7" />
      <g fill="#0d0f11">
        <rect x="10" y="12" width="2" height="14" />
        <rect x="8" y="16" width="6" height="8" />
        <rect x="20" y="6" width="2" height="18" />
        <rect x="18" y="10" width="6" height="10" />
      </g>
    </svg>
  );
}
