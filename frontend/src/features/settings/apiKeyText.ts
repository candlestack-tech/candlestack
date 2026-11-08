import type { ApiKey, Scope } from '@/api';
import { calendarDay, formatDate, formatRecentDay, formatTime, type DateStyle } from '@/lib/dates';

// What the API keys card (design/mockups/api-mcp, keysCard) writes about a key: its scopes in
// plain words, when it was last used and when it stops working.

// The scopes in the API's order, with what each one lets a key do.
export const SCOPES: { scope: Scope; label: string; description: string }[] = [
  { scope: 'read', label: 'Read', description: 'See your backtests, models and market data' },
  { scope: 'backtests', label: 'Run backtests', description: 'Start, rename and star backtests' },
  { scope: 'models', label: 'Manage models', description: 'Upload, rename and delete your models' },
  { scope: 'publish', label: 'Publish', description: 'Publish and unpublish backtests in Experiments' },
];

// A key's name is 1 to 40 characters in the app (the API takes up to 80).
export const NAME_MAX = 40;

// How long a new key works: 30 days, 90 days (the default), a year or until it is revoked.
export type Expiry = '30' | '90' | '365' | 'never';

export const EXPIRIES: { value: Expiry; label: string; days: number | null }[] = [
  { value: '30', label: '30 days', days: 30 },
  { value: '90', label: '90 days', days: 90 },
  { value: '365', label: '1 year', days: 365 },
  { value: 'never', label: 'Never', days: null },
];

const DAY = 86_400_000;

// Calendar days from `now` to `date` in the user's zone: 0 today, 1 tomorrow, -1 yesterday.
function daysUntil(date: Date, now: number, timeZone: string): number {
  const number = ({ year, month, day }: ReturnType<typeof calendarDay>) =>
    Date.UTC(Number(year), Number(month) - 1, Number(day)) / DAY;
  return number(calendarDay(date, timeZone)) - number(calendarDay(new Date(now), timeZone));
}

// A key whose expiry has passed does not work: it stays listed, greyed, until it is deleted, and
// does not count toward the limit.
export const isExpired = (key: ApiKey, now: number) => key.expires_at !== null && Date.parse(key.expires_at) <= now;

// When a request last signed in with the key (or a connected app): "12 min ago" within the hour,
// "Today 02:00" and "Yesterday 21:40", then the date; null if it never did.
export function lastUsedText(key: Pick<ApiKey, 'last_used_at'>, style: DateStyle, now: number): string | null {
  if (key.last_used_at === null) return null;
  const date = new Date(key.last_used_at);
  const minutes = Math.floor((now - date.getTime()) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const day = formatRecentDay(date, style, new Date(now), true);
  return day === 'Today' || day === 'Yesterday' ? `${day} ${formatTime(date, style)}` : formatDate(date, style);
}

// The sentence of the Revoke question about the key's last use.
export function lastUsedSentence(key: ApiKey, style: DateStyle, now: number): string {
  const used = lastUsedText(key, style, now);
  if (used === null) return 'It was never used.';
  return `It was last used ${used.replace(/^(Today|Yesterday)/, (word) => word.toLowerCase())}.`;
}

type ExpiryText = { text: string; tone?: 'warn' | 'down' };

// When the key stops working: "No expiry", "Expires 1 Feb 2027", in amber within a week ("Expires
// in 5 days"), in red once passed ("Expired 1 Nov 2026").
export function expiryText(key: ApiKey, style: DateStyle, now: number): ExpiryText {
  if (key.expires_at === null) return { text: 'No expiry' };
  const date = new Date(key.expires_at);
  if (isExpired(key, now)) return { text: `Expired ${formatDate(date, style)}`, tone: 'down' };
  const days = daysUntil(date, now, style.timeZone);
  if (days > 7) return { text: `Expires ${formatDate(date, style)}` };
  const when = days <= 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`;
  return { text: `Expires ${when}`, tone: 'warn' };
}

// The hint under the expiry choice: the day a new key would stop working.
export function expiryHint(expiry: Expiry, style: DateStyle, now: number): string {
  const days = EXPIRIES.find((choice) => choice.value === expiry)?.days ?? null;
  if (days === null) return 'Works until you revoke it.';
  return `On ${formatDate(new Date(now + days * DAY), style)}. Create a new key then.`;
}

// What a new key may do and for how long, under its name: "read · backtests · 90 days".
export function newKeySummary(scopes: Scope[], expiry: Expiry): string {
  const label = expiry === 'never' ? 'no expiry' : EXPIRIES.find((choice) => choice.value === expiry)?.label;
  return [...scopes, label].join(' · ');
}

export type Example = 'api' | 'claude-code' | 'cursor';

// The Try it examples of a new key, with the key and this host filled in.
export function exampleText(example: Example, secret: string, origin: string): string {
  if (example === 'claude-code')
    return `claude mcp add --transport http candlestack ${origin}/mcp \\\n  --header "Authorization: Bearer ${secret}"`;
  if (example === 'cursor')
    return [
      '{',
      '  "mcpServers": {',
      '    "candlestack": {',
      `      "url": "${origin}/mcp",`,
      `      "headers": { "Authorization": "Bearer ${secret}" }`,
      '    }',
      '  }',
      '}',
    ].join('\n');
  return `# Who am I\ncurl ${origin}/api/v1/me \\\n  -H "Authorization: Bearer ${secret}"`;
}
