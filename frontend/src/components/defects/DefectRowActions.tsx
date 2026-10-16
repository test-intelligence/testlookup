/**
 * W06 DEF-01: a defect row's **⋯ Defect actions** menu: Change status…,
 * Assign…, Link Jira issue… (Change Jira issue… once linked), each opening its
 * dialog. Every change goes through `PATCH /projects/{project_id}/defects/{id}`
 * and is recorded in the activity log; `onUpdated` receives the row as the
 * server reads it afterwards, so the page replaces it without a reload.
 *
 * QA engineers and above only (the route is `require_role(QA_ENGINEER)`): a
 * viewer or a tester gets no menu at all rather than three actions that
 * answer 403. A row without its `project_id` (an older payload) has no project
 * to change it in, so it gets no menu either.
 */
import { useState } from 'react'
import OverflowMenu, { type OverflowItem } from '@/components/ui/OverflowMenu'
import { usePermissions } from '@/hooks/usePermissions'
import type { DefectItem } from '@/types/analytics'
import AssignDefectDialog from './AssignDefectDialog'
import DefectStatusDialog from './DefectStatusDialog'
import LinkJiraIssueDialog from './LinkJiraIssueDialog'

export interface DefectRowActionsProps {
  /** Reads id, project_id, resolution_status, assignee_user_id, assignee_name, owner_team, jira_ticket_id. */
  defect: DefectItem
  /** Called once with `DefectPatchResponse.defect` after a successful PATCH. */
  onUpdated: (row: DefectItem) => void
}

type OpenDialog = 'status' | 'assign' | 'link' | null

export default function DefectRowActions({ defect, onUpdated }: DefectRowActionsProps) {
  const { isQaEngineer } = usePermissions()
  const [open, setOpen] = useState<OpenDialog>(null)
  if (!isQaEngineer || !defect.project_id) return null

  const close = () => setOpen(null)
  const items: OverflowItem[] = [
    { label: 'Change status…', onClick: () => setOpen('status') },
    { label: 'Assign…', onClick: () => setOpen('assign') },
    { label: defect.jira_ticket_id ? 'Change Jira issue…' : 'Link Jira issue…', onClick: () => setOpen('link') },
  ]

  return (
    <span className="inline-flex align-middle" data-defect-actions="">
      <OverflowMenu label="Defect actions" items={items} />
      {open === 'status' && <DefectStatusDialog defect={defect} onClose={close} onUpdated={onUpdated} />}
      {open === 'assign' && <AssignDefectDialog defect={defect} onClose={close} onUpdated={onUpdated} />}
      {open === 'link' && <LinkJiraIssueDialog defect={defect} onClose={close} onUpdated={onUpdated} />}
    </span>
  )
}
