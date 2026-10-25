import type { SVGProps } from 'react';

// The design's own line icons where lucide draws a different one (mockups/app: the sidebar, the
// account menu, the theme toggle, the cards of Settings, the Models page). The icons lucide draws
// the same way (the dashboard's grid, the API docs' brackets, sign out's door, the plus, the sun,
// the cross) stay lucide's. Same frame as lucide's: a 24 px grid, 2 px round strokes in the text
// colour, 24 px unless a class sizes it.

type IconProps = Omit<SVGProps<SVGSVGElement>, 'children'>;

function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

// Backtests: a line rising to an arrow.
export function BacktestsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 17l5-5 4 4 8-8" />
      <path d="M14 8h6v6" />
    </Icon>
  );
}

// Models: a box with sharp corners.
export function ModelsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 2l9 5v10l-9 5-9-5V7z" />
      <path d="M12 22V12M21 7l-9 5-9-5" />
    </Icon>
  );
}

// Settings, in the sidebar and the account menu: a ring with eight spokes.
export function SettingsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1" />
    </Icon>
  );
}

// Home page: a house with a door.
export function HomeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 10.5L12 3l9 7.5" />
      <path d="M5 9v12h14V9" />
      <path d="M10 21v-6h4v6" />
    </Icon>
  );
}

// Upload model: an arrow up out of a tray.
export function UploadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 15V4M7 9l5-5 5 5" />
      <path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" />
    </Icon>
  );
}

// Last backtests: a clock.
export function ClockIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </Icon>
  );
}

// Best backtests: a cup with two handles on its stem.
export function TrophyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z" />
      <path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3" />
    </Icon>
  );
}

// Compare backtests: two arrows passing each other.
export function SwapIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h13l-3-3M20 17H7l3 3" />
    </Icon>
  );
}

// The dark theme: a crescent moon.
export function MoonIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </Icon>
  );
}

// Settings, Profile: a head over shoulders.
export function UserIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c1-4 4-6 8-6s7 2 8 6" />
    </Icon>
  );
}

// Settings, Sign-in: a key.
export function KeyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="8" cy="14" r="4" />
      <path d="M11 11l9-9M16 6l3 3M14 8l2 2" />
    </Icon>
  );
}

// Settings, Backtest defaults: three sliders.
export function SlidersIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" />
      <circle cx="16" cy="6" r="2" />
      <circle cx="10" cy="12" r="2" />
      <circle cx="18" cy="18" r="2" />
    </Icon>
  );
}

// Settings, Appearance: a circle, half of it filled.
export function AppearanceIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" />
    </Icon>
  );
}

// Settings, Data and storage: a database drum.
export function DataIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </Icon>
  );
}

// Settings, MCP server: a plug.
export function PlugIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0z" />
      <path d="M12 18v4" />
    </Icon>
  );
}

// Settings, Account: a warning triangle.
export function WarnIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3l10 18H2z" />
      <path d="M12 10v5M12 18h.01" />
    </Icon>
  );
}

// The directions of a trade (Settings, Backtest defaults): up for long, down for short.
export function ArrowUpIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 19V5M6 11l6-6 6 6" />
    </Icon>
  );
}

export function ArrowDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M6 13l6 6 6-6" />
    </Icon>
  );
}
