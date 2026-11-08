import { DashboardCard } from '@/components/DashboardCard';
import { ComingSoonTag } from '@/components/ComingSoon';
import { PlugIcon } from '@/components/icons';

import { ApiKeysCard } from './ApiKeysCard';
import { Code, SettingsLead, SettingsRow } from './rows';

// The tag at the right of a card's title (.cs-tag.st-soon): smaller, flat.
const SOON = <ComingSoonTag className="px-[11px] py-[5px] text-[11px]/[normal]" />;

// The API keys, and the MCP server still to come. The server's address is this host's, so stage
// shows stage's.
export function ApiSection() {
  return (
    <>
      <ApiKeysCard />
      <DashboardCard icon={PlugIcon} title="MCP server" aside={SOON}>
        <SettingsLead>
          Use CandleStack from Claude or ChatGPT. Ask about your backtests, start new ones and compare them in a chat.
          You sign in with your CandleStack account.
        </SettingsLead>
        <SettingsRow label="Server URL">
          <Code>{`${window.location.origin}/mcp`}</Code>
        </SettingsRow>
      </DashboardCard>
    </>
  );
}
