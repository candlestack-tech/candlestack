import { ArrowRight, Check } from 'lucide-react';
import type { ComponentType } from 'react';

import { BUTTON_SIZES, OUTLINE_BUTTON } from '@/components/buttonStyles';
import { ClaudeLogo, CursorLogo, OpenAILogo, VSCodeLogo } from '@/components/clientLogos';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { Checks, ExampleLine } from './parts';
import { Section, SectionIntro } from './Section';

// The MCP server's install guide on the website, per client.
const INSTALL = 'https://candlestack.tech/docs/mcp/install/';

const CLIENTS: readonly { name: string; Logo: ComponentType<{ className?: string }> }[] = [
  { name: 'Claude Code', Logo: ClaudeLogo },
  { name: 'claude.ai', Logo: ClaudeLogo },
  { name: 'ChatGPT', Logo: OpenAILogo },
  { name: 'Codex', Logo: OpenAILogo },
  { name: 'Cursor', Logo: CursorLogo },
  { name: 'VS Code', Logo: VSCodeLogo },
];

const POINTS = [
  'Run backtests and sweeps over a grid of settings',
  'Upload a model through a one-time link',
  'Compare results and publish the best one',
  'The same actions in the API for your scripts',
];

// A sample session, written here: the tools are the MCP server's own (docs/mcp-tools.json).
const CALLS = [
  ['start_backtest', 'lstm-renko · AAPL · 1h'],
  ['start_backtest', 'lstm-kagi · AAPL · 1h'],
  ['wait_for_backtest', '2 done in 41 s'],
  ['compare_backtests', '2 backtests'],
] as const;

const RESULTS = [
  { name: 'lstm-kagi', values: ['+18.4 %', '−7.9 %', '1.21'], best: true },
  { name: 'lstm-renko', values: ['+11.2 %', '−12.6 %', '0.74'], best: false },
] as const;

// As in the compare example: the best value in green, the worst in red.
const MARKS = {
  best: 'bg-[color-mix(in_srgb,var(--up)_14%,transparent)] font-semibold text-[color-mix(in_srgb,var(--up)_85%,var(--ink))]',
  worst: 'bg-[color-mix(in_srgb,var(--down)_14%,transparent)] font-semibold text-down',
};

const CELL = 'truncate px-2.5 py-[7px] whitespace-nowrap';

function Clients() {
  return (
    <ul aria-label="Supported clients" className="mt-[18px] flex flex-wrap gap-1.5">
      {CLIENTS.map(({ name, Logo }) => (
        <li
          key={name}
          className="inline-flex h-7 items-center gap-[7px] rounded-full border bg-background pr-2.5 pl-2 text-[12.5px] font-medium"
        >
          <Logo className="size-[15px]" />
          {name}
        </li>
      ))}
    </ul>
  );
}

// The comparison the agent sends back, in small.
function Comparison() {
  return (
    <table
      aria-label="Example comparison"
      className="w-full table-fixed overflow-hidden rounded-lg text-[12.5px] outline outline-border"
    >
      <thead>
        <tr className="text-[11.5px] text-muted-foreground">
          <th scope="col" className={cn(CELL, 'w-[31%] text-left font-normal')}>
            <span className="sr-only">Backtest</span>
          </th>
          {['Return', 'Max DD', 'Sharpe'].map((head) => (
            <th key={head} scope="col" className={cn(CELL, 'text-left font-normal')}>
              {head}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {RESULTS.map(({ name, values, best }) => (
          <tr key={name} className="border-t">
            <th scope="row" className={cn(CELL, 'text-left font-normal')}>
              {name}
            </th>
            {values.map((value) => (
              <td key={value} className={cn(CELL, 'font-mono tabular-nums', best ? MARKS.best : MARKS.worst)}>
                {value}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// An agent at work: the question, the tools it calls, its answer and the comparison.
function Chat() {
  return (
    <div className="overflow-hidden rounded-xl border bg-background">
      <div className="flex items-center gap-2 border-b px-3.5 py-2.5 text-[12.5px] text-muted-foreground">
        <ClaudeLogo className="size-[15px]" />
        Claude · candlestack
        <span className="ml-auto inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="size-[7px] rounded-full bg-up" />
          Connected
        </span>
      </div>
      <div className="flex flex-col gap-3.5 p-4 text-[13.5px]">
        <p className="max-w-[82%] self-end rounded-[12px_12px_4px_12px] bg-surface-2 px-[13px] py-2.5 max-sm:max-w-full">
          Run my two LSTMs on AAPL 1h over 2025, the Renko one and the Kagi one, and tell me which holds up.
        </p>
        <ul className="flex flex-col gap-[5px] font-mono text-xs text-muted-foreground">
          {CALLS.map(([tool, args]) => (
            <li key={args} className="flex min-w-0 items-center gap-2">
              <Check aria-hidden="true" strokeWidth={2.5} className="size-[13px] flex-none text-up" />
              <code className="whitespace-nowrap text-foreground">{tool}</code>
              <span className="min-w-0 truncate">{args}</span>
            </li>
          ))}
        </ul>
        <p className="max-w-[64ch]">
          The Kagi model holds up better. It made more with a smaller drawdown. The Renko one traded twice as often, so
          fees took a bigger share.
        </p>
        <Comparison />
        {/* Part of the sample, so not a link. */}
        <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-primary">
          Open the comparison in CandleStack
          <ArrowRight aria-hidden="true" className="size-3.5" />
        </span>
      </div>
    </div>
  );
}

// Connecting an AI client to the MCP server: which clients, what an agent can do, and a sample
// session (design#42, mockups/home-update).
export function Agents() {
  return (
    <Section id="agents" split>
      <div>
        <SectionIntro
          eyebrow="Agents"
          title="Let your AI agent run the backtests"
          text="Connect your AI client to the CandleStack MCP server and ask in plain words. The agent finds the data, runs your models and explains what it found."
        />
        <Clients />
        <Checks items={POINTS} />
        <div className="mt-5 flex flex-wrap items-center gap-2.5">
          <Button asChild variant="outline" className={cn(BUTTON_SIZES.default, OUTLINE_BUTTON)}>
            <a href={INSTALL} target="_blank" rel="noopener">
              Install the server
            </a>
          </Button>
          {/* Same origin, so stage links to the stage API. */}
          <a
            href="/api/v1/docs"
            target="_blank"
            rel="noopener"
            className="inline-flex items-center gap-1.5 text-[13px] font-medium text-primary hover:underline"
          >
            API reference
            <ArrowRight aria-hidden="true" className="size-3.5" />
          </a>
        </div>
      </div>
      <div>
        <Chat />
        <ExampleLine>Sample session, not market data</ExampleLine>
      </div>
    </Section>
  );
}
