// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('Tailwind build input boundary', () => {
  it('limits automatic utility scanning to static local app sources', () => {
    const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
    expect(css).toContain("@import 'tailwindcss' source(none);")
    const sources = [...css.matchAll(/@source\s+['"]([^'"]+)['"]/g)].map(match => match[1])
    expect(sources).toEqual(['../index.html', './'])
    expect(css).not.toMatch(/@config|@tailwind/)
  })
})
