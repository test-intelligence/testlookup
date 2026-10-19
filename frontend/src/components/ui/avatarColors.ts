/**
 * The ONE avatar colour map (W29a UIX-01 L3): the top bar, the profile page's
 * picker and the test-management owners all read it.
 *
 * Initials are ALWAYS `fg` on `bg` — a status `-ink` (or the info ink) on its
 * own tint, >= 4.68:1 in every theme. White initials are gone: on a light
 * theme's 15 % tint they were about 1.3:1, i.e. blank.
 *
 * Values are CSS strings applied with `style` (constant tokens chosen from a
 * fixed map by a key: CSP-safe). The stored keys (`users.avatar_color`, set on
 * the profile page) are unchanged.
 */
import { RESULT_STATUS_TOKEN, type StatusToken } from './statusVocabulary'

export const AVATAR_COLOR_KEYS = [
  'slate', 'red', 'orange', 'amber', 'lime', 'emerald', 'teal', 'cyan', 'blue', 'violet', 'fuchsia', 'pink',
] as const

export type AvatarColorKey = (typeof AVATAR_COLOR_KEYS)[number]

export interface AvatarColor {
  /** The avatar's fill. */
  bg: string
  /** Its initials. */
  fg: string
  /** A selection ring / outline in the same family. */
  ring: string
}

const fromStatus = (t: StatusToken): AvatarColor => ({ bg: t.tint, fg: t.ink, ring: t.hue })

const SLATE: AvatarColor = { bg: 'var(--color-bg-hover)', fg: 'var(--color-text)', ring: 'var(--color-border-light)' }
const DANGER = fromStatus(RESULT_STATUS_TOKEN.failed)
const WARNING = fromStatus(RESULT_STATUS_TOKEN.broken)
const SUCCESS = fromStatus(RESULT_STATUS_TOKEN.passed)
const INFO: AvatarColor = { bg: 'var(--color-accent-muted)', fg: 'var(--ui-info-ink)', ring: 'var(--color-accent)' }
const VIOLET = fromStatus(RESULT_STATUS_TOKEN.flaky)

export const AVATAR_COLORS: Readonly<Record<AvatarColorKey, AvatarColor>> = {
  slate: SLATE,
  red: DANGER,
  orange: WARNING,
  amber: WARNING,
  lime: SUCCESS,
  emerald: SUCCESS,
  teal: SUCCESS,
  cyan: INFO,
  blue: INFO,
  violet: VIOLET,
  fuchsia: VIOLET,
  pink: VIOLET,
}

const isKey = (key: string): key is AvatarColorKey => (AVATAR_COLOR_KEYS as readonly string[]).includes(key)

/** A stored avatar key's colours; an unknown or missing key → slate. */
export function avatarColor(key: string | null | undefined): AvatarColor {
  const k = (key ?? '').trim().toLowerCase()
  return isKey(k) ? AVATAR_COLORS[k] : SLATE
}

/** The buckets a NAME hashes into (people with no stored colour, e.g. test owners). */
export const NAME_AVATAR_KEYS: readonly AvatarColorKey[] = ['blue', 'violet', 'teal', 'orange', 'red']

/** A stable colour for a name: the same name always gets the same bucket. */
export function avatarColorForName(name: string): AvatarColor {
  let h = 0
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0
  return AVATAR_COLORS[NAME_AVATAR_KEYS[Math.abs(h) % NAME_AVATAR_KEYS.length]]
}
