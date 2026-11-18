import { describe, expect, it } from 'vitest';

import { initials } from './initials';

describe('initials', () => {
  it('takes the first letters of the first and the last word', () => {
    expect(initials('Ada Lovelace', 'ada@example.com')).toBe('AL');
    expect(initials('Filip  Jozef  Hodoň', 'f@example.com')).toBe('FH');
  });

  it('takes one letter of a one-word name, in upper case', () => {
    expect(initials('adalovelace', 'ada@example.com')).toBe('A');
  });

  it('takes the first letter of the email when the name is empty', () => {
    expect(initials('  ', 'zoe@example.com')).toBe('Z');
  });

  it('keeps letters with diacritics and characters outside the BMP whole', () => {
    expect(initials('ľubomír šťastný', 'l@example.com')).toBe('ĽŠ');
    expect(initials('𝓐lex Kim', 'a@example.com')).toBe('𝓐K');
  });
});
