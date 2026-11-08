/**
 * W38 (W31 F-2, contract §4, D-8/D-9): the copy and the pure rules of Settings › SSO › SCIM Tokens' "When your
 * identity provider deletes a user" (`ScimDeleteActionSection`). React text only.
 */
import { isAxiosError } from 'axios'

import { extractErrorMessage } from '@/services/apiErrors'
import type { ScimDeleteAction } from '@/services/ssoService'
import { formatCompactDateTime } from '@/utils/formatters'

export const SCIM_DELETE_TITLE = 'When your identity provider deletes a user'
export const SCIM_DELETE_INTRO = 'What a SCIM delete does to the account. It applies to every SCIM token.'
export const SCIM_DELETE_OPTIONS: readonly { value: ScimDeleteAction; label: string; hint: string }[] = [
  {
    value: 'deactivate',
    label: 'Deactivate the account (default)',
    hint: 'The person can no longer sign in; the account and its data stay, and an administrator can reactivate it.',
  },
  {
    value: 'erase',
    label: "Erase the person's data — cannot be undone",
    hint: 'The account is erased as by "Erase permanently" on Users: name, username and e-mail become a pseudonym, and audit records are kept.',
  },
]
export const SCIM_ERASE_WARNING =
  'Every delete from your identity provider will then erase that person at once, and an erasure cannot be undone. ' +
  'When someone cannot be erased (a service account, a running export, the last administrator) the account is ' +
  'deactivated instead and the refusal is listed under Identity Events.'
export const SCIM_ERASE_ACK = 'I understand an erasure cannot be undone'
export const SCIM_DELETE_SAVE = 'Save'
export const SCIM_DELETE_SAVED = 'Saved.'
export const SCIM_DELETE_LOAD_FAILED = 'Could not load the SCIM delete setting.'
export const SCIM_DELETE_SAVE_FAILED = 'The setting could not be saved.'

/** "Last changed Sep 21, 11:59 by Morgan Admin." — or null when the setting was never saved. */
export function lastChangedLine(updatedAt: string | null | undefined, by: string | null | undefined): string | null {
  if (!updatedAt) return null
  const who = by?.trim()
  return who ? `Last changed ${formatCompactDateTime(updatedAt)} by ${who}.` : `Last changed ${formatCompactDateTime(updatedAt)}.`
}

/** A stored value the UI does not know reads as the default (the server does the same). */
export function knownAction(action: string | null | undefined): ScimDeleteAction {
  return action === 'erase' ? 'erase' : 'deactivate'
}

/** The refusal's sentence (`detail.message` of the `{code, message}` body), never the Axios words. */
export function refusalMessage(err: unknown): string {
  if (isAxiosError(err)) {
    const data: unknown = err.response?.data
    const detail = data && typeof data === 'object' ? (data as { detail?: unknown }).detail : undefined
    if (detail !== undefined && detail !== null) return extractErrorMessage(detail, SCIM_DELETE_SAVE_FAILED)
  }
  return SCIM_DELETE_SAVE_FAILED
}
