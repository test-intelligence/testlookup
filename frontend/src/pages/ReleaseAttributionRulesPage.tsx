/**
 * Release attribution rules (W11 REL-09): `/releases/attribution-rules`.
 *
 * Rung 3 of the attribution ladder had an API (and a preview) but no page, so
 * a project could only get rules by calling the API. This page, for ONE
 * project (All Projects renders the project prompt), in order:
 *
 *   1. the ladder in the order the server applies it, and that a rule never
 *      moves a run already linked (D13: a run no rule matches still lands by
 *      the cutoff window or the active release);
 *   2. the rules in the order they are tried (the list's server order);
 *   3. for a QA lead, New rule / Edit open ONE in-page form above the table
 *      (`useInlineSlot`), with Preview — "which release would these runs land
 *      in" — and Delete asks in a strip under its row (no modal, no confirm());
 *   4. a footer counting the Unattributed runs among the last preview's runs.
 *
 * Reads on load: the rules and the field list (+ the cached top-bar release
 * list for the target suggestions — no new request). A QA engineer, tester or
 * viewer reads the explanation and the table only.
 */
import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import AttributionRuleForm from '@/components/attribution/AttributionRuleForm'
import {
  EMPTY_RULES,
  FOOTER_WITHOUT_PREVIEW,
  LADDER_STEPS,
  LOAD_ERROR,
  PAGE_SUBTITLE,
  PAGE_TITLE,
  PROJECT_PROMPT,
  effectiveFields,
  emptyForm,
  fieldLabel,
  formFromRule,
  matchesText,
  targetNote,
  unattributedFooter,
} from '@/components/attribution/attributionModel'
import { helpTopicParam } from '@/components/help/helpTopics'
import { UNATTRIBUTED_RELEASE, RELEASE_PARAM } from '@/components/layout/ReleasePicker'
import DataUnavailable from '@/components/ui/DataUnavailable'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import InlineConfirm from '@/components/ui/inPage/InlineConfirm'
import InlineRowSection from '@/components/ui/inPage/InlineRowSection'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useAttributionRuleFields, useAttributionRules } from '@/hooks/useAttributionRules'
import { usePermissions } from '@/hooks/usePermissions'
import { useReleases } from '@/hooks/useReleases'
import { attributionRulesService } from '@/services/attributionRulesService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { AttributionPreviewResponse, AttributionRule, AttributionRuleInput } from '@/types/attributionRules'

const HELP_TOPIC = helpTopicParam('/releases/attribution-rules')

type SlotKey = 'new' | `edit:${string}`

export default function ReleaseAttributionRulesPage() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null

  if (!projectId) {
    return (
      <div className="space-y-4">
        <PageHeader compact title={PAGE_TITLE} subtitle={PAGE_SUBTITLE} helpTopic={HELP_TOPIC} />
        <ProjectRequiredEmptyState description={PROJECT_PROMPT} />
      </div>
    )
  }
  return <AttributionRulesForProject key={projectId} projectId={projectId} />
}

function AttributionRulesForProject({ projectId }: { projectId: string }) {
  const { isQaLead } = usePermissions()
  const { data, error: loadError, isLoading, mutate } = useAttributionRules(projectId)
  const { data: fieldData } = useAttributionRuleFields(projectId)
  // The top bar's own list, read as cached (no request of its own): the Release suggestions and the
  // "(created on first match)" note. Undefined while unknown, and then nothing is claimed.
  const { data: releaseData } = useReleases(undefined, { cached: true })
  const releases = releaseData?.items
  const releaseNames = useMemo(() => releases?.map((r) => r.name), [releases])

  const fields = effectiveFields(fieldData?.fields)
  const rules: AttributionRule[] = data?.items ?? []
  const slot = useInlineSlot<SlotKey>()
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)
  const [lastPreview, setLastPreview] = useState<AttributionPreviewResponse | null>(null)

  const editing = slot.openKey?.startsWith('edit:') ? rules.find((r) => `edit:${r.id}` === slot.openKey) ?? null : null
  const columns = isQaLead ? 7 : 6

  async function save(body: AttributionRuleInput) {
    if (editing) await attributionRulesService.update(projectId, editing.id, body)
    else await attributionRulesService.create(projectId, body)
    toast.success(editing ? 'Rule saved.' : 'Rule created.')
    slot.close()
    await mutate()
  }

  async function toggle(rule: AttributionRule) {
    setToggling(rule.id)
    try {
      await attributionRulesService.update(projectId, rule.id, { is_enabled: !rule.is_enabled })
      await mutate()
    } catch {
      toast.error('Could not change the rule.')
    } finally {
      setToggling(null)
    }
  }

  async function remove(rule: AttributionRule) {
    setDeleting(true)
    try {
      await attributionRulesService.remove(projectId, rule.id)
      setConfirmingDelete(null)
      if (slot.openKey === `edit:${rule.id}`) slot.close()
      toast.success('Rule deleted.')
      await mutate()
    } catch {
      toast.error('Could not delete the rule.')
    } finally {
      setDeleting(false)
    }
  }

  const footer = unattributedFooter(lastPreview)

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title={PAGE_TITLE}
        subtitle={PAGE_SUBTITLE}
        helpTopic={HELP_TOPIC}
        actions={
          isQaLead ? (
            <button type="button" className="btn-primary" onClick={() => slot.open('new')}>
              New rule
            </button>
          ) : undefined
        }
      />

      <section aria-label="How a run gets its release" className="max-w-[72ch] text-sm text-[var(--color-text-secondary)]">
        <p className="m-0">
          When a run arrives, it is linked to the first of these that applies:{' '}
          {LADDER_STEPS.map((step, i) => (
            <Fragment key={step}>
              {i > 0 && <span aria-hidden="true"> · </span>}
              <span className="whitespace-nowrap">{i + 1}. {step}</span>
            </Fragment>
          ))}
          . A run that no rule matches still lands by 4 or 5, and a rule never moves a run that is already linked.
        </p>
      </section>

      {isQaLead && slot.openKey !== null && (slot.openKey === 'new' || editing) && (
        <AttributionRuleForm
          key={slot.openKey}
          mode={editing ? 'edit' : 'new'}
          initial={editing ? formFromRule(editing) : emptyForm(fields)}
          ruleId={editing?.id ?? null}
          fields={fields}
          releaseNames={releaseNames}
          slot={slot}
          onClose={slot.close}
          onPreview={(body, ruleId) => attributionRulesService.preview(projectId, body, ruleId)}
          onPreviewed={setLastPreview}
          onSave={save}
        />
      )}

      <div data-primary="">
        {loadError && !data ? (
          <div className="space-y-2">
            <p className="m-0 text-sm font-medium text-[var(--color-text)]">{LOAD_ERROR}</p>
            <DataUnavailable error={loadError} onRetry={() => void mutate()} testId="attribution-rules-unavailable" />
          </div>
        ) : isLoading && !data ? (
          <div className="flex justify-center py-10">
            <LoadingSpinner size="lg" />
          </div>
        ) : rules.length === 0 ? (
          <p className="card m-0 text-sm text-[var(--color-text-muted)]">{EMPTY_RULES}</p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
            <Table density="dense" className="table-fixed" aria-label="Attribution rules">
              {/* Fixed columns, planned for DejaVu Sans (CI): 72 · 170 · 200 · 170 · 96 · 88 · 128 = 924 px
                  ≤ 976 − 40 at 1280; each header label (11 px uppercase + px-3) fits its column. */}
              <colgroup>
                <col className="w-[72px]" />
                <col className="w-[170px]" />
                <col className="w-[200px]" />
                <col className="w-[170px]" />
                <col className="w-24" />
                <col className="w-[88px]" />
                {isQaLead && <col className="w-32" />}
              </colgroup>
              <thead>
                <tr>
                  <Th numeric>Order</Th>
                  <Th>Rule</Th>
                  <Th>Matches</Th>
                  <Th>Release</Th>
                  <Th numeric>Priority</Th>
                  <Th>Enabled</Th>
                  {isQaLead && <Th><span className="sr-only">Actions</span></Th>}
                </tr>
              </thead>
              <tbody>
                {rules.map((rule, index) => {
                  const note = targetNote(rule.target_release_name, releases)
                  return (
                    <Fragment key={rule.id}>
                      <Tr interactive={false} data-rule-id={rule.id} className={rule.is_enabled ? undefined : 'opacity-60'}>
                        <Td numeric>{index + 1}</Td>
                        <Td className="truncate font-medium text-[var(--color-text)]" title={rule.name}>{rule.name}</Td>
                        <Td className="truncate" title={matchesText(rule, fields)}>
                          {fieldLabel(rule.match_field, fields)}{' '}
                          <span className="font-mono text-xs text-[var(--color-text)]">{rule.match_pattern}</span>
                        </Td>
                        <Td className="truncate" title={note ? `${rule.target_release_name} ${note}` : rule.target_release_name}>
                          {rule.target_release_name}
                          {note && <span className="text-xs text-[var(--color-text-muted)]"> {note}</span>}
                        </Td>
                        <Td numeric>{rule.priority}</Td>
                        <Td>
                          {isQaLead ? (
                            <input
                              type="checkbox"
                              className="h-4 w-4"
                              checked={rule.is_enabled}
                              disabled={toggling === rule.id}
                              aria-label={`Enabled: ${rule.name}`}
                              onChange={() => void toggle(rule)}
                            />
                          ) : (
                            rule.is_enabled ? 'Yes' : 'No'
                          )}
                        </Td>
                        {isQaLead && (
                          <Td className="whitespace-nowrap text-right">
                            <button
                              type="button"
                              className="text-xs font-medium text-[var(--color-accent)] hover:underline"
                              aria-label={`Edit rule ${rule.name}`}
                              onClick={() => {
                                setConfirmingDelete(null)
                                slot.open(`edit:${rule.id}`)
                              }}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="ml-3 text-xs font-medium text-[var(--ui-danger-ink)] hover:underline"
                              aria-label={`Delete rule ${rule.name}`}
                              onClick={() => setConfirmingDelete(rule.id)}
                            >
                              Delete
                            </button>
                          </Td>
                        )}
                      </Tr>
                      {isQaLead && confirmingDelete === rule.id && (
                        <InlineRowSection colSpan={columns}>
                          <InlineConfirm
                            message={
                              <>
                                Delete rule &quot;<strong>{rule.name}</strong>&quot;? Runs it already matched stay where they are.
                              </>
                            }
                            confirmLabel="Delete"
                            tone="danger"
                            busy={deleting}
                            onConfirm={() => void remove(rule)}
                            onCancel={() => setConfirmingDelete(null)}
                          />
                        </InlineRowSection>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </Table>
          </div>
        )}
      </div>

      <p data-attribution-footer="" className="m-0 text-sm text-[var(--color-text-muted)]">
        {footer.text === FOOTER_WITHOUT_PREVIEW ? (
          footer.text
        ) : (
          <>
            {footer.text}
            {footer.count > 0 && (
              <>
                {' '}
                <Link
                  to={`/runs?${RELEASE_PARAM}=${UNATTRIBUTED_RELEASE}`}
                  className="text-[var(--color-accent)] hover:underline"
                >
                  Show them in Runs
                </Link>
              </>
            )}
          </>
        )}
      </p>
    </div>
  )
}
