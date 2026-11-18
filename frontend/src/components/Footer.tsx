import { PHONE_SIDES_16 } from '@/components/pageInsets';
import { cn } from '@/lib/utils';

// Where to read more about CandleStack. A new tab, so following one does not lose the page.
const links = [
  // Same origin, so stage links to the stage API.
  { name: 'API docs', href: '/api/v1/docs' },
  { name: 'Status', href: 'https://ops.candlestack.tech' },
  { name: 'candlestack.tech', href: 'https://candlestack.tech' },
];

// The attributions the chart library and the logo source ask for (docs/frontend.md, Licenses).
const credits = [
  { text: 'Charts by', name: 'TradingView Lightweight Charts™', href: 'https://www.tradingview.com/' },
  { text: 'Logos provided by', name: 'Logo.dev', href: 'https://logo.dev' },
];

// Solid, so the background grid stops above it. The credits go right from sm up; on a phone they
// wrap under the links. 24 px at the sides; in the app 16 px on a phone, as the page above it
// (mockups/app; mockups/home keeps 24 px). A link is 24 px high for a finger, its padding taking
// no room from the line (an inline link's padding never does).
export function Footer({ app = false }: { app?: boolean }) {
  return (
    <footer className="border-t bg-background">
      <div
        className={cn(
          'mx-auto flex max-w-[75rem] flex-wrap gap-x-5 gap-y-1 py-4 pr-[max(24px,env(safe-area-inset-right,0px))] pl-[max(24px,env(safe-area-inset-left,0px))] text-xs leading-normal text-muted-foreground',
          app && PHONE_SIDES_16,
        )}
      >
        <span>CandleStack</span>
        {links.map(({ name, href }) => (
          <a key={href} href={href} target="_blank" rel="noopener" className="-my-[3px] py-[3px] hover:text-foreground">
            {name}
          </a>
        ))}
        <div className="flex flex-wrap gap-x-5 gap-y-1 sm:ml-auto">
          {credits.map(({ text, name, href }) => (
            <p key={href}>
              {text} {/* Underlined: the link is the part of the sentence that credits them. */}
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                className="py-[3px] underline underline-offset-2 hover:text-foreground"
              >
                {name}
              </a>
            </p>
          ))}
        </div>
      </div>
    </footer>
  );
}
