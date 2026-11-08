/**
 * /users?tab=erasure-requests (W38, W31 F-1, contract §4; admin only): the people who asked to be erased.
 *
 * Filter Pending / Decided / All (default Pending; the server orders pending oldest first, then decided newest
 * first). A pending row: "Review" opens the person's EXISTING Personal data section under the row (preview + the
 * typed confirm; its erasure POST is the approval and closes the request in the same commit, D-5 — no second erase
 * UI), "Decline" opens the reason select (the four fixed codes) + "Decline request". The reader's own request: the
 * `own_request` note, no actions. The list re-reads after an erasure or a decline; a row whose section is open stays
 * drawn until it closes (an erasure moves it out of Pending at once). In the page: no modal, no native confirm.
 * Pattern: `PendingInvitationsTab`.
 */
import { Fragment, useCallback, useState } from 'react'

import PersonalDataSection from '@/components/subjectRights/PersonalDataSection'
import InlineRowSection from '@/components/ui/inPage/InlineRowSection'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th } from '@/components/ui/Table'
import { useErasureRequests } from '@/hooks/useErasureRequests'
import { useSlotCloser } from '@/hooks/useSlotCloser'
import { refreshUsers } from '@/hooks/useUserManagement'
import type { ErasureRequestFilter, ErasureRequestRead } from '@/types/subjectRights'
import { formatCompactDateTime } from '@/utils/formatters'
import { DeclineRequestForm } from './DeclineRequestForm'
import {
  asUserRow,
  decidedLabel,
  DECLINE,
  declineLabel,
  DEFAULT_FILTER,
  EMPTY_LINES,
  FILTER_GROUP_LABEL,
  FILTER_LABELS,
  FILTERS,
  LOAD_FAILED,
  OWN_REQUEST_NOTE,
  personName,
  REVIEW,
  reviewLabel,
  rowActions,
  statusLabel,
  withOpenRow,
} from './erasureRequestsModel'

/** `review:<id>` = the person's Personal data section; `decline:<id>` = the reason select. */
type RowForm = `review:${string}` | `decline:${string}`
const COLUMNS = 5

const FILTER_ON =
  'rounded px-2.5 py-1 text-xs font-medium bg-[var(--color-accent-muted)] text-[var(--color-accent-ink)]'
const FILTER_OFF =
  'rounded px-2.5 py-1 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'

export function ErasureRequestsTab() {
  const [filter, setFilter] = useState<ErasureRequestFilter>(DEFAULT_FILTER)
  const { data, error, isLoading, mutate } = useErasureRequests(filter, true)
  const rowSlot = useInlineSlot<RowForm>()
  const closeRow = useSlotCloser(rowSlot)
  // The row whose section is open (drawn even after a re-read drops it), and the rows whose Review erased.
  const [openRow, setOpenRow] = useState<ErasureRequestRead | null>(null)
  const [erasedFromReview, setErasedFromReview] = useState<ReadonlySet<string>>(() => new Set())

  const open = openRow && rowSlot.openKey?.endsWith(`:${openRow.id}`) ? openRow : null
  const rows = withOpenRow(Array.isArray(data?.items) ? data.items : [], open, erasedFromReview)

  const refresh = useCallback(() => {
    void mutate()
  }, [mutate])

  function openSection(row: ErasureRequestRead, form: 'review' | 'decline') {
    setOpenRow(row)
    rowSlot.open(`${form}:${row.id}`)
  }

  function changeFilter(next: ErasureRequestFilter) {
    if (next === filter) return
    rowSlot.guard(() => {
      rowSlot.close()
      setFilter(next)
    })
  }

  return (
    <div className="flex flex-col gap-3" data-erasure-requests-tab="">
      <div role="group" aria-label={FILTER_GROUP_LABEL} className="flex w-fit items-center gap-1 rounded-lg border border-[var(--color-border)] p-0.5">
        {FILTERS.map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={filter === value}
            onClick={() => changeFilter(value)}
            className={filter === value ? FILTER_ON : FILTER_OFF}
          >
            {FILTER_LABELS[value]}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><LoadingSpinner /></div>
      ) : error && rows.length === 0 ? (
        <p className="m-0 text-sm text-[var(--ui-danger-ink)]">{LOAD_FAILED}</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
          <Table data-testid="erasure-requests">
            <thead>
              <tr className="border-b border-[var(--color-border)]">
                <Th>Person</Th>
                <Th>Asked</Th>
                <Th>Status</Th>
                <Th>Decided</Th>
                <Th>Actions</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {rows.map((row) => {
                const reviewKey: RowForm = `review:${row.id}`
                const declineKey: RowForm = `decline:${row.id}`
                const actions = rowActions(row)
                const name = personName(row)
                return (
                  <Fragment key={row.id}>
                    <tr data-erasure-request={row.id} data-erasure-request-status={row.status}>
                      <Td className="px-4 py-3">
                        <div className="max-w-[185px] truncate font-medium text-[var(--color-text)]" title={name}>{name}</div>
                        {row.subject_username && (
                          <div className="max-w-[185px] truncate text-xs text-[var(--color-text-muted)]" title={row.subject_username}>
                            {row.subject_username}
                          </div>
                        )}
                      </Td>
                      <Td className="px-4 py-3 text-[var(--color-text-secondary)]">{formatCompactDateTime(row.requested_at)}</Td>
                      <Td className="px-4 py-3 text-[var(--color-text)]" data-erasure-request-status-cell="">{statusLabel(row)}</Td>
                      <Td className="px-4 py-3 text-[var(--color-text-secondary)]">{decidedLabel(row)}</Td>
                      <Td className="px-4 py-3">
                        {actions === 'review' && (
                          <div className="flex items-center gap-2">
                            <button type="button" className="btn-secondary btn-sm" aria-label={reviewLabel(row)} onClick={() => openSection(row, 'review')}>
                              {REVIEW}
                            </button>
                            <button
                              type="button"
                              className="text-xs px-2 py-1 rounded text-[var(--ui-danger)] hover:underline"
                              aria-label={declineLabel(row)}
                              onClick={() => openSection(row, 'decline')}
                            >
                              {DECLINE}
                            </button>
                          </div>
                        )}
                        {actions === 'own' && (
                          <span className="text-xs text-[var(--color-text-muted)]" data-erasure-request-own="">{OWN_REQUEST_NOTE}</span>
                        )}
                      </Td>
                    </tr>
                    {rowSlot.openKey === reviewKey && (
                      <InlineRowSection colSpan={COLUMNS} dataAttributes={{ 'data-erasure-request-review': row.id }}>
                        <PersonalDataSection
                          user={asUserRow(row)}
                          slot={rowSlot}
                          onClose={() => closeRow(reviewKey)}
                          onUsersChanged={() => {
                            // The erasure POST (and its `done`) closed this request (D-5): re-read the queue and users.
                            setErasedFromReview((prev) => (prev.has(row.id) ? prev : new Set(prev).add(row.id)))
                            refresh()
                            refreshUsers()
                          }}
                        />
                      </InlineRowSection>
                    )}
                    {rowSlot.openKey === declineKey && (
                      <InlineRowSection colSpan={COLUMNS} dataAttributes={{ 'data-erasure-request-decline': row.id }}>
                        <DeclineRequestForm
                          row={row}
                          slot={rowSlot}
                          onClose={() => closeRow(declineKey)}
                          onDeclined={() => {
                            closeRow(declineKey)
                            refresh()
                          }}
                        />
                      </InlineRowSection>
                    )}
                  </Fragment>
                )
              })}
              {rows.length === 0 && (
                <tr>
                  <Td colSpan={COLUMNS} className="px-4 py-8 text-center text-[var(--color-text-muted)]">
                    {EMPTY_LINES[filter]}
                  </Td>
                </tr>
              )}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  )
}
