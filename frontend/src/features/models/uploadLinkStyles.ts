import { ACCENT_BUTTON, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { cn } from '@/lib/utils';

// The buttons of the upload page's cards (design/mockups/api-mcp, .one .acts .btn): 40 px high,
// the first in the accent.
const CARD_BUTTON = 'h-10 px-4';
export const PRIMARY = cn(CARD_BUTTON, ACCENT_BUTTON);
export const SECONDARY = cn(CARD_BUTTON, OUTLINE_BUTTON);
