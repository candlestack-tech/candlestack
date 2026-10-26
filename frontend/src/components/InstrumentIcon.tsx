import { ChartCandlestick } from 'lucide-react';
import { useState } from 'react';

import { instrumentIconUrl } from '@/api';
import { cn } from '@/lib/utils';

// 16 to 24 px next to a symbol in the app (InstrumentSymbol), 32 px in the instrument list and the
// home page's top five, 64 px on the instrument page.
const SIZE = { 16: 'size-4', 20: 'size-5', 24: 'size-6', 32: 'size-8', 64: 'size-16' } as const;
type Size = keyof typeof SIZE;

type TileProps = { id: string; size: Size; failed: boolean; onError: () => void };

// The instrument's logo, or the backend's square fallback, on a white tile rounded by 10 % of its
// size. The image fills the whole tile, clipped to its corners, and the 1 px hairline is drawn
// above it, so a logo that fills its whole box (Apple's) never covers it. A 1 px gap between the
// image and the edge would show the white tile as a stripe on one side at 125 % or 150 % display
// scaling, where the browser rounds the image and the hairline differently. White in both themes,
// so black logos such as Apple's stay visible in dark mode. The fixed size keeps rows from jumping
// while it loads; the alt is empty because the symbol is always next to it. When the image does
// not load at all (e.g. a 502 during a deploy), the tile draws the backend's fallback itself: the
// white chart-candlestick on dark grey.
export function IconTile({ id, size, failed, onError }: TileProps) {
  return (
    <span
      className={cn(
        'relative block shrink-0 overflow-hidden rounded-[10%] bg-white',
        'after:pointer-events-none after:absolute after:inset-0 after:rounded-[inherit] after:inset-ring after:inset-ring-icon-ring',
        SIZE[size],
      )}
    >
      {failed ? (
        <span className="absolute inset-0 grid place-items-center bg-icon-fallback text-white">
          <ChartCandlestick aria-hidden className="size-[56%]" />
        </span>
      ) : (
        <img
          src={instrumentIconUrl(id)}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          onError={onError}
          className="absolute inset-0 size-full object-contain"
        />
      )}
    </span>
  );
}

// Remembers the id whose image failed, so the next instrument in the same place (the header on
// another instrument page) tries its own image again.
export function InstrumentIcon({ id, size }: { id: string; size: Size }) {
  const [failed, setFailed] = useState<string | null>(null);
  return <IconTile id={id} size={size} failed={failed === id} onError={() => setFailed(id)} />;
}
