/**
 * W29a UIX-01 L3: the ONE avatar map. Initials are always `fg` on `bg` (an ink
 * on its own tint); white initials and literal colours are gone.
 */
import { describe, expect, it } from 'vitest'
import { AVATAR_COLORS, AVATAR_COLOR_KEYS, NAME_AVATAR_KEYS, avatarColor, avatarColorForName } from './avatarColors'

describe('AVATAR_COLORS', () => {
  it('has every stored key (the profile picker’s twelve)', () => {
    expect(Object.keys(AVATAR_COLORS).sort()).toEqual([...AVATAR_COLOR_KEYS].sort())
    expect(AVATAR_COLOR_KEYS).toHaveLength(12)
  })

  it('every bg / fg / ring is a token string: never white, never a hex', () => {
    for (const key of AVATAR_COLOR_KEYS) {
      for (const [part, value] of Object.entries(AVATAR_COLORS[key])) {
        expect(value, `${key}.${part}`).toMatch(/^(var\(--|color-mix\()/)
        expect(value, `${key}.${part}`).not.toMatch(/white|#|\brgba?\(/i)
      }
    }
  })

  it('initials read an ink (or the body text), never the bare hue', () => {
    expect(AVATAR_COLORS.red.fg).toBe('var(--status-failed-ink)')
    expect(AVATAR_COLORS.amber.fg).toBe('var(--status-broken-ink)')
    expect(AVATAR_COLORS.emerald.fg).toBe('var(--status-passed-ink)')
    expect(AVATAR_COLORS.blue.fg).toBe('var(--ui-info-ink)')
    expect(AVATAR_COLORS.pink.fg).toBe('var(--status-flaky-ink)')
    expect(AVATAR_COLORS.slate).toEqual({ bg: 'var(--color-bg-hover)', fg: 'var(--color-text)', ring: 'var(--color-border-light)' })
    expect(AVATAR_COLORS.red.bg).toBe('var(--status-failed-bg)')
    expect(AVATAR_COLORS.red.ring).toBe('var(--status-failed)')
  })
})

describe('avatarColor', () => {
  it('reads a stored key, case-insensitively; an unknown or missing key is slate', () => {
    expect(avatarColor('teal')).toBe(AVATAR_COLORS.teal)
    expect(avatarColor(' Violet ')).toBe(AVATAR_COLORS.violet)
    for (const other of ['magenta', '', null, undefined, 'constructor']) {
      expect(avatarColor(other), String(other)).toBe(AVATAR_COLORS.slate)
    }
  })
})

describe('avatarColorForName', () => {
  it('is stable: the same name always gets the same colour', () => {
    for (const name of ['Ada Lovelace', 'grace', 'x', '']) {
      expect(avatarColorForName(name)).toBe(avatarColorForName(name))
    }
  })

  it('spreads names over its five buckets, and only those', () => {
    const allowed = new Set(NAME_AVATAR_KEYS.map((k) => AVATAR_COLORS[k]))
    expect(NAME_AVATAR_KEYS).toEqual(['blue', 'violet', 'teal', 'orange', 'red'])
    const seen = new Set<unknown>()
    for (let i = 0; i < 200; i++) {
      const colour = avatarColorForName(`owner-${i}@example.test`)
      expect(allowed.has(colour)).toBe(true)
      seen.add(colour)
    }
    expect(seen.size).toBe(NAME_AVATAR_KEYS.length)
  })
})
