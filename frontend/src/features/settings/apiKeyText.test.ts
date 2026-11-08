import { describe, expect, it } from 'vitest';

import type { ApiKey } from '@/api';
import { dateStyleOf } from '@/lib/dates';

import {
  exampleText,
  expiryHint,
  expiryText,
  isExpired,
  lastUsedSentence,
  lastUsedText,
  newKeySummary,
} from './apiKeyText';

// 8 Nov 2026, 12:00 UTC: 07:00 in New York, the zone of the API's default settings.
const NOW = Date.parse('2026-11-08T12:00:00Z');
const STYLE = dateStyleOf();

const key = (fields: Partial<ApiKey> = {}): ApiKey => ({
  id: 'key_7f3k2m4qx4',
  name: 'Claude Code on my laptop',
  prefix: 'cs_7f3k2m4q',
  scopes: ['read', 'backtests'],
  created_at: '2026-11-03T10:00:00Z',
  last_used_at: null,
  expires_at: null,
  ...fields,
});

describe('lastUsedText', () => {
  it('is null for a key never used', () => {
    expect(lastUsedText(key(), STYLE, NOW)).toBeNull();
  });

  it('counts minutes within the hour', () => {
    expect(lastUsedText(key({ last_used_at: '2026-11-08T11:59:40Z' }), STYLE, NOW)).toBe('just now');
    expect(lastUsedText(key({ last_used_at: '2026-11-08T11:48:00Z' }), STYLE, NOW)).toBe('12 min ago');
  });

  it('gives the time today and yesterday, then the date, in the user’s zone', () => {
    expect(lastUsedText(key({ last_used_at: '2026-11-08T07:00:00Z' }), STYLE, NOW)).toBe('Today 02:00');
    expect(lastUsedText(key({ last_used_at: '2026-11-08T02:40:00Z' }), STYLE, NOW)).toBe('Yesterday 21:40');
    expect(lastUsedText(key({ last_used_at: '2026-10-30T15:00:00Z' }), STYLE, NOW)).toBe('30 Oct 2026');
  });
});

describe('lastUsedSentence', () => {
  it('says when the key was last used, in the middle of a sentence', () => {
    expect(lastUsedSentence(key(), STYLE, NOW)).toBe('It was never used.');
    expect(lastUsedSentence(key({ last_used_at: '2026-11-08T07:00:00Z' }), STYLE, NOW)).toBe(
      'It was last used today 02:00.',
    );
    expect(lastUsedSentence(key({ last_used_at: '2026-11-08T11:48:00Z' }), STYLE, NOW)).toBe(
      'It was last used 12 min ago.',
    );
  });
});

describe('expiryText', () => {
  it('says No expiry, or the day the key stops working', () => {
    expect(expiryText(key(), STYLE, NOW)).toEqual({ text: 'No expiry' });
    expect(expiryText(key({ expires_at: '2027-02-01T10:00:00Z' }), STYLE, NOW)).toEqual({
      text: 'Expires 1 Feb 2027',
    });
  });

  it('turns amber within a week', () => {
    const within = (iso: string) => expiryText(key({ expires_at: iso }), STYLE, NOW);
    expect(within('2026-11-13T12:00:00Z')).toEqual({ text: 'Expires in 5 days', tone: 'warn' });
    expect(within('2026-11-15T12:00:00Z')).toEqual({ text: 'Expires in 7 days', tone: 'warn' });
    expect(within('2026-11-09T12:00:00Z')).toEqual({ text: 'Expires tomorrow', tone: 'warn' });
    expect(within('2026-11-08T20:00:00Z')).toEqual({ text: 'Expires today', tone: 'warn' });
  });

  it('turns red once passed, and the key counts as expired', () => {
    const gone = key({ expires_at: '2026-11-01T12:00:00Z' });
    expect(expiryText(gone, STYLE, NOW)).toEqual({ text: 'Expired 1 Nov 2026', tone: 'down' });
    expect(isExpired(gone, NOW)).toBe(true);
    expect(isExpired(key({ expires_at: '2026-11-08T12:00:01Z' }), NOW)).toBe(false);
    expect(isExpired(key(), NOW)).toBe(false);
  });
});

describe('expiryHint', () => {
  it('gives the day a new key would stop working', () => {
    expect(expiryHint('30', STYLE, NOW)).toBe('On 8 Dec 2026. Create a new key then.');
    expect(expiryHint('90', STYLE, NOW)).toBe('On 6 Feb 2027. Create a new key then.');
    expect(expiryHint('365', STYLE, NOW)).toBe('On 8 Nov 2027. Create a new key then.');
    expect(expiryHint('never', STYLE, NOW)).toBe('Works until you revoke it.');
  });
});

describe('newKeySummary', () => {
  it('lists the scopes and how long the key works', () => {
    expect(newKeySummary(['read', 'backtests'], '90')).toBe('read · backtests · 90 days');
    expect(newKeySummary(['read'], '365')).toBe('read · 1 year');
    expect(newKeySummary(['read'], 'never')).toBe('read · no expiry');
  });
});

describe('exampleText', () => {
  it('fills the key and the host into each example', () => {
    const origin = 'https://app.candlestack.tech';
    expect(exampleText('api', 'cs_secret', origin)).toBe(
      '# Who am I\ncurl https://app.candlestack.tech/api/v1/me \\\n  -H "Authorization: Bearer cs_secret"',
    );
    expect(exampleText('claude-code', 'cs_secret', origin)).toBe(
      'claude mcp add --transport http candlestack https://app.candlestack.tech/mcp \\\n  --header "Authorization: Bearer cs_secret"',
    );
    expect(JSON.parse(exampleText('cursor', 'cs_secret', origin))).toEqual({
      mcpServers: {
        candlestack: {
          url: 'https://app.candlestack.tech/mcp',
          headers: { Authorization: 'Bearer cs_secret' },
        },
      },
    });
  });
});
