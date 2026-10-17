/**
 * W06 DEF-01: a defect row's **⋯ Defect actions** menu: Change status…,
 * Assign…, Link Jira issue… (Change Jira issue… once linked), each opening its
 * form. Every change goes through `PATCH /projects/{project_id}/defects/{id}`
 * and is recorded in the activity log; `onUpdated` receives the row as the
 * server reads it afterwards, so the page replaces it without a reload.
 *
 * W27 PAN-04: the forms open IN THE PAGE. A menu sits in the row's Actions
 * cell, and a cell cannot host a full-width row, so the row CONTROLS it:
 * `action` + `onAction` (the menu reports the chosen item, the row renders
 * `DefectActionForm` in an `InlineRowSection` under itself). Without
 * `onAction` the menu keeps rendering the form right after itself.
 *
 * QA engineers and above only (the route is `require_role(QA_ENGINEER)`): a
 * viewer or a tester gets no menu at all rather than three actions that
 * answer 403. A row without its `project_id` (an older payload) has no project
 * to change it in, so it gets no menu either.
 */
import { useState } from 'react'
import OverflowMenu, { type OverflowItem } from '@/components/ui/OverflowMenu'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { usePermissions } from '@/hooks/usePermissions'
import type { DefectItem } from '@/types/analytics'
import AssignDefectDialog from './AssignDefectDialog'
import DefectStatusDialog from './DefectStatusDialog'
import LinkJiraIssueDialog from './LinkJiraIssueDialog'

/** The three row actions (the menu's items, in order). */
export type DefectAction = 'status' | 'assign' | 'link'

export interface DefectRowActionsProps {
  /** Reads id, project_id, resolution_status, assignee_user_id, assignee_name, owner_team, jira_ticket_id. */
  defect: DefectItem
  /** Called once with `DefectPatchResponse.defect` after a successful PATCH. */
  onUpdated: (row: DefectItem) => void
  /** Controlled by the row: the action whose form is open (the row renders it). */
  action?: DefectAction | null
  /** Given: a menu item reports its action here and this component renders no form. */
  onAction?: (action: DefectAction | null) => void
}

export interface DefectActionFormProps {
  defect: DefectItem
  action: DefectAction
  onClose: () => void
  onUpdated: (row: DefectItem) => void
  /** The table's slot: one form open per table, and a typed form asks before it is replaced. */
  slot?: InlineSlot
}

/** The form of one row action (W06 DEF-01's three forms), wherever its row puts it. */
export function DefectActionForm({ defect, action, onClose, onUpdated, slot }: DefectActionFormProps) {
  if (action === 'status') return <DefectStatusDialog defect={defect} onClose={onClose} onUpdated={onUpdated} slot={slot} />
  if (action === 'assign') return <AssignDefectDialog defect={defect} onClose={onClose} onUpdated={onUpdated} slot={slot} />
  return <LinkJiraIssueDialog defect={defect} onClose={onClose} onUpdated={onUpdated} slot={slot} />
}

export default function DefectRowActions({ defect, onUpdated, action, onAction }: DefectRowActionsProps) {
  const { isQaEngineer } = usePermissions()
  const [own, setOwn] = useState<DefectAction | null>(null)
  if (!isQaEngineer || !defect.project_id) return null

  const controlled = onAction !== undefined
  const choose = (next: DefectAction | null) => (controlled ? onAction(next) : setOwn(next))
  const items: OverflowItem[] = [
    { label: 'Change status…', onClick: () => choose('status') },
    { label: 'Assign…', onClick: () => choose('assign') },
    { label: defect.jira_ticket_id ? 'Change Jira issue…' : 'Link Jira issue…', onClick: () => choose('link') },
  ]
  // Controlled: the row renders the form under itself; the menu only marks which one is open.
  const open = controlled ? null : own

  return (
    <span className="inline-flex align-middle" data-defect-actions="" data-defect-action-open={(controlled ? action : own) ?? undefined}>
      <OverflowMenu label="Defect actions" items={items} />
      {open && <DefectActionForm defect={defect} action={open} onClose={() => setOwn(null)} onUpdated={onUpdated} />}
    </span>
  )
}
