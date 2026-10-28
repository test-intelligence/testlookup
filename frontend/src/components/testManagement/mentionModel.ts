/**
 * W21d ACT-02: the comment composer's `@mention` model. Pure: no React, no
 * request, no store — the composer and its tests import one copy of the rules.
 *
 * The server is the authority (`backend/app/services/activity/mentions.py`):
 * an `@` starts a mention when it begins the text or follows a character
 * outside `[A-Za-z0-9_.@-]` (so `email@alice` is not one), and a name resolves
 * only to an active, non-synthetic, non-service member of the case's project
 * other than the author. The suggestions apply the same member rule, so the
 * list never offers a name the server would leave as text. What is inserted is
 * `@username` — never an e-mail, never an id.
 */

/** A character that keeps an `@` inside a word (`email@alice`): the server's class. */
const WORD_BEFORE = /[A-Za-z0-9_.@-]/
const WHITESPACE = /\s/
/** A username is at most 100 characters (`users.username`); a longer token is no mention. */
const MAX_TOKEN = 100
export const MAX_SUGGESTIONS = 6

/** What the composer reads of a `GET /api/v1/projects/{id}/members` row. */
export interface MentionMember {
  user_id: string
  username: string
  full_name?: string | null
  is_active?: boolean
  is_synthetic?: boolean
  is_service_account?: boolean
}

export interface MentionOption {
  username: string
  /** `Full Name · @username`, or `@username` without a full name. */
  label: string
}

/** One entry of the POST answer's `mentions` (the members the server notified). */
export interface PostedMention {
  user_id: string
  username: string
  display_name: string
}

/**
 * The `@token` the caret sits in, if any: `{ start }` is the `@`'s index and
 * `query` what is typed after it up to the caret. No whitespace may sit between
 * the `@` and the caret. An `@` inside a word is skipped, so an SSO name being
 * typed (`@bob@corp`) still reads from its first `@`.
 */
export function activeMentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const end = Math.min(Math.max(0, caret), text.length)
  for (let i = end - 1; i >= 0 && end - i <= MAX_TOKEN + 1; i -= 1) {
    const ch = text[i]
    if (WHITESPACE.test(ch)) return null
    if (ch !== '@') continue
    if (i === 0 || !WORD_BEFORE.test(text[i - 1])) return { start: i, query: text.slice(i + 1, end) }
  }
  return null
}

/** The server's member rule: active, not synthetic, not a service account, not the signed-in user. */
export function isMentionable(member: MentionMember, selfId: string | null): boolean {
  return Boolean(member.username)
    && member.is_active !== false
    && member.is_synthetic !== true
    && member.is_service_account !== true
    && member.user_id !== selfId
}

function labelOf(member: MentionMember): string {
  const name = (member.full_name ?? '').trim()
  return name ? `${name} · @${member.username}` : `@${member.username}`
}

/**
 * At most six suggestions for `query`: a prefix match on the username or the
 * full name first, then a substring match, each group in username order.
 */
export function mentionOptions(
  members: readonly MentionMember[] | null | undefined,
  query: string,
  selfId: string | null,
): MentionOption[] {
  const q = query.toLowerCase()
  const prefix: MentionMember[] = []
  const inner: MentionMember[] = []
  for (const member of members ?? []) {
    if (!isMentionable(member, selfId)) continue
    const username = member.username.toLowerCase()
    const name = (member.full_name ?? '').toLowerCase()
    if (username.startsWith(q) || (name !== '' && name.startsWith(q))) prefix.push(member)
    else if (username.includes(q) || name.includes(q)) inner.push(member)
  }
  const byName = (a: MentionMember, b: MentionMember) => a.username.localeCompare(b.username)
  return [...prefix.sort(byName), ...inner.sort(byName)]
    .slice(0, MAX_SUGGESTIONS)
    .map(member => ({ username: member.username, label: labelOf(member) }))
}

/**
 * Replace the `@token` from `start` to `caret` with `@username ` and put the
 * caret after it (an existing space after the caret is reused, not doubled).
 */
export function insertMention(text: string, start: number, caret: number, username: string): { text: string; caret: number } {
  const before = text.slice(0, start)
  const after = text.slice(caret)
  const spaced = after.startsWith(' ')
  const inserted = `@${username}${spaced ? '' : ' '}`
  return { text: before + inserted + after, caret: before.length + inserted.length + (spaced ? 1 : 0) }
}

/** The success toast: `Comment added`, or `Comment added · notified @alice, @bob`. */
export function postedToast(mentions: readonly PostedMention[] | null | undefined): string {
  const names = (Array.isArray(mentions) ? mentions : [])
    .filter(m => typeof m?.username === 'string' && m.username !== '')
    .map(m => `@${m.username}`)
  return names.length ? `Comment added · notified ${names.join(', ')}` : 'Comment added'
}
