// @vitest-environment node
import { describe, expect, it } from 'vitest'
import tailwindConfig from '../tailwind.config.js'

describe('Tailwind build input boundary', () => {
  it('uses bounded repository globs without nested brace expansion', () => {
    // braces has no patched release for its recursive stack-exhaustion advisory.
    // Keep the actual build inputs bounded; this does not make untrusted source
    // execution safe or protect arbitrary callers of the installed dependency.
    expect(Array.isArray(tailwindConfig.content)).toBe(true)
    expect(tailwindConfig.content.length).toBeGreaterThan(0)
    for (const pattern of tailwindConfig.content) {
      expect(typeof pattern).toBe('string')
      expect(pattern.startsWith('./')).toBe(true)
      expect(pattern).not.toContain('..')
      expect(pattern.length).toBeLessThanOrEqual(512)
      let depth = 0
      for (const character of pattern) {
        if (character === '{') depth += 1
        if (character === '}') depth -= 1
        expect(depth).toBeGreaterThanOrEqual(0)
        expect(depth).toBeLessThanOrEqual(1)
      }
      expect(depth).toBe(0)
    }
  })
})
