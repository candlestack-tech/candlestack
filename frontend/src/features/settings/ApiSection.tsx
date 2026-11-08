import { ApiKeysCard } from './ApiKeysCard';
import { McpCard } from './McpCard';

// The API keys, and the MCP server with the apps connected to it.
export function ApiSection() {
  return (
    <>
      <ApiKeysCard />
      <McpCard />
    </>
  );
}
