import { describe, it, expect } from 'vitest';

import ru from './ru.json';
import uz from './uz.json';

/** Every key of one locale, flattened to dotted paths, so a missing branch is reported by name
 * rather than as "the objects differ". */
function paths(value: unknown, prefix = ''): string[] {
  if (typeof value !== 'object' || value === null) {
    return [prefix];
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    paths(child, prefix ? `${prefix}.${key}` : key)
  );
}

describe('i18n locales', () => {
  it('carry exactly the same set of keys', () => {
    // A key present in one locale and missing in the other renders as the raw path
    // (`getNestedValue` returns it verbatim) — the member sees "profile.tickets.blockedAge" on
    // their screen and nobody finds out, because the language nobody on the team reads is the one
    // that breaks. Written when step 5 added seven keys to both files.
    const inRu = paths(ru).sort();
    const inUz = paths(uz).sort();

    expect(inUz.filter((key) => !inRu.includes(key))).toEqual([]);
    expect(inRu.filter((key) => !inUz.includes(key))).toEqual([]);
  });

  it('leave no value empty', () => {
    // An empty string passes the parity check above and still shows the member nothing.
    const empty = (locale: object) =>
      paths(locale).filter((path) => {
        const value = path
          .split('.')
          .reduce<unknown>((node, key) => (node as Record<string, unknown>)[key], locale);
        return typeof value === 'string' && value.trim() === '';
      });

    expect(empty(ru)).toEqual([]);
    expect(empty(uz)).toEqual([]);
  });
});
