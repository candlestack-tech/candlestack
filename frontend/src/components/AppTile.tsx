import { useState, type ComponentType } from 'react';

import type { AppClient } from '@/api';
import { cn } from '@/lib/utils';

import { ClaudeLogo, OpenAILogo } from './clientLogos';

// The apps whose mark the app draws itself, by the host they return to. Anyone can register an app
// called Claude, but only Claude returns to claude.ai: the API checks the redirect URI against the
// registered ones, so the host is the app's own.
const MARKS: Record<string, ComponentType<{ className?: string }>> = {
  'claude.ai': ClaudeLogo,
  'chatgpt.com': OpenAILogo,
  'chat.openai.com': OpenAILogo,
};

type Props = { client: AppClient; className?: string; markClassName?: string };

// An app on a neutral tile (design/mockups/api-mcp, .kr-ic.app and .pair .app): the mark of a
// known app, else the logo the app gave (sent without a referrer), else its initial. A logo that
// does not load falls back to the initial. Decorative: the app's name is always written next to it.
// The caller sizes the tile, the mark and the initial's type.
export function AppTile({ client, className, markClassName }: Props) {
  const [broken, setBroken] = useState<string | null>(null);
  const Mark = MARKS[client.redirect_host];
  const logo = client.logo_uri !== null && client.logo_uri !== broken ? client.logo_uri : null;
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid flex-none place-items-center overflow-hidden bg-surface-2 font-sans font-bold text-foreground',
        className,
      )}
    >
      {Mark ? (
        <Mark className={markClassName} />
      ) : logo ? (
        <img
          src={logo}
          alt=""
          referrerPolicy="no-referrer"
          className={cn('object-contain', markClassName)}
          onError={() => setBroken(logo)}
        />
      ) : (
        client.name.trim().charAt(0).toUpperCase()
      )}
    </span>
  );
}
