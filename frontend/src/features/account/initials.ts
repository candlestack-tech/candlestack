// The first character of a word, a whole code point, so "Ťažký" gives "Ť" and an emoji stays whole.
const first = (word: string | undefined) => (word === undefined ? '' : (Array.from(word)[0] ?? ''));

// The avatar's letters (our own drawing, never the provider's picture): the first letters of the
// first and the last word of the name, "Ada Lovelace" → "AL"; one for a one-word name; the
// email's first letter when the name is empty.
export function initials(name: string, email: string): string {
  const words = name
    .trim()
    .split(/\s+/)
    .filter((word) => word !== '');
  const letters = words.length > 1 ? first(words[0]) + first(words.at(-1)) : first(words[0] ?? email);
  return letters.toLocaleUpperCase();
}
