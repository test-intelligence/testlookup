/**
 * W13 TCL-06 — the Requirements an authored case links (traceability "lite",
 * D10: keys a person types, nothing inferred). Shown at the end of the case's
 * Details tab only while `test_case_requirement_traceability` is ON (the host
 * mounts it; this component reads `useCaseRequirements(caseId, true)`).
 *
 * Each link is a row: the key (a button that filters the case list by its
 * NORMALISED key), its title, its type and who added it. TESTER+ in one
 * project (`canEdit`) on a non-retired case may **Add requirement** (an
 * in-page form) and **Remove** one (asked in the row, `InlineConfirm`). The
 * server's 409 / 422 sentence renders in the form.
 *
 * Keys and titles are React TEXT; nothing builds an `href` or a `style` from them.
 */
import { Fragment, useId, useState, type FormEvent } from 'react'
import { Plus, X } from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineConfirm } from '@/components/ui/inPage/InlineConfirm'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { refreshLibraryHealth, useCaseRequirements } from '@/hooks/useTestLibrary'
import { testLibraryService } from '@/services/testLibraryService'
import type { ManagedTestCase } from '@/types/test-management'
import type { RequirementLink } from '@/types/test-library'
import { serverSentence } from './importCasesModel'
import {
  ADD_FAILED,
  ADD_TITLE,
  ADD_TITLE_ID,
  LINK_TYPE_OPTIONS,
  REMOVE_FAILED,
  REQUIREMENTS_EMPTY,
  REQUIREMENTS_ERROR,
  REQUIREMENTS_LOADING,
  REQUIREMENTS_TITLE,
  REQUIREMENT_KEY_MAX,
  REQUIREMENT_TITLE_MAX,
  RETIRED_STATES,
  addedByLine,
  keyProblem,
  linkTypeLabel,
} from './requirementsModel'

export interface CaseRequirementsSectionProps {
  caseItem: ManagedTestCase
  /** TESTER+ with one project (the page's `canWrite`); a retired case is read-only here as well. */
  canEdit: boolean
  /** Filter the case list by this key (`requirement=<normalised>` and its removable chip). */
  onFilterRequirement: (normalizedKey: string) => void
}

const KEY_BUTTON = 'min-w-0 truncate text-left font-mono text-xs text-[var(--color-accent-ink)] hover:underline'
const TYPE_PILL =
  'inline-flex items-center whitespace-nowrap rounded-full border border-[var(--color-border-light)] bg-[var(--color-bg-secondary)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]'
const ICON_BUTTON =
  'inline-flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40'

function AddRequirementForm({ caseId, onClose, onAdded }: { caseId: string; onClose: () => void; onAdded: (link: RequirementLink) => void }) {
  const keyId = useId()
  const titleId = useId()
  const typeId = useId()
  const [key, setKey] = useState('')
  const [title, setTitle] = useState('')
  const [linkType, setLinkType] = useState('verifies')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const problem = keyProblem(key)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const link = await testLibraryService.addRequirement(caseId, {
        requirement_key: key.trim(),
        requirement_title: title.trim() || null,
        link_type: linkType,
      })
      onAdded(link)
    } catch (err) {
      setError(serverSentence(err, ADD_FAILED))
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title={ADD_TITLE}
      titleId={ADD_TITLE_ID}
      headingLevel={3}
      onClose={onClose}
      canClose={!busy}
      busy={busy}
      onSubmit={(event) => void submit(event)}
      dataAttributes={{ 'data-requirement-add': '' }}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn-primary inline-flex items-center gap-2" disabled={busy || key.trim().length === 0}>
            {busy && <LoadingSpinner size="sm" />}
            Add requirement
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div>
          <label htmlFor={keyId} className="mb-1 block text-xs text-[var(--color-text-muted)]">Key</label>
          <input id={keyId} className="input w-full font-mono" value={key} maxLength={REQUIREMENT_KEY_MAX}
            placeholder="REQ-PAY-07" onChange={(event) => setKey(event.target.value)} disabled={busy} />
        </div>
        <div>
          <label htmlFor={titleId} className="mb-1 block text-xs text-[var(--color-text-muted)]">Title (optional)</label>
          <input id={titleId} className="input w-full" value={title} maxLength={REQUIREMENT_TITLE_MAX}
            onChange={(event) => setTitle(event.target.value)} disabled={busy} />
        </div>
        <div>
          <label htmlFor={typeId} className="mb-1 block text-xs text-[var(--color-text-muted)]">Link type</label>
          <select id={typeId} className="input w-full" value={linkType} onChange={(event) => setLinkType(event.target.value)} disabled={busy}>
            {LINK_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
        {error && <p role="alert" data-requirement-error="" className="m-0 text-sm text-[var(--ui-danger-ink)]">{error}</p>}
      </div>
    </InlineFormSection>
  )
}

export default function CaseRequirementsSection({ caseItem, canEdit, onFilterRequirement }: CaseRequirementsSectionProps) {
  const headingId = useId()
  const { data, error, isLoading, mutate } = useCaseRequirements(caseItem.id, true)
  const editable = canEdit && !RETIRED_STATES.has(caseItem.status)
  const [adding, setAdding] = useState(false)
  // The link whose Remove is asking (one strip at a time); nothing is sent until its Remove.
  const [confirming, setConfirming] = useState<string | null>(null)
  const [removing, setRemoving] = useState(false)
  const [removeError, setRemoveError] = useState<{ id: string; text: string } | null>(null)
  const links = data?.items ?? []

  function changed() {
    void mutate()
    void refreshLibraryHealth()
  }

  async function remove(link: RequirementLink) {
    setRemoving(true)
    setRemoveError(null)
    try {
      await testLibraryService.removeRequirement(caseItem.id, link.id)
      setConfirming(null)
      toast.success(`Removed ${link.requirement_key}`)
      changed()
    } catch (err) {
      setRemoveError({ id: link.id, text: serverSentence(err, REMOVE_FAILED) })
    } finally {
      setRemoving(false)
    }
  }

  return (
    <section aria-labelledby={headingId} data-case-requirements="" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 id={headingId} className="eyebrow m-0">{REQUIREMENTS_TITLE}</h3>
        {editable && !adding && (
          <button type="button" data-requirement-add-open="" className="btn-secondary btn-sm inline-flex items-center gap-1" onClick={() => setAdding(true)}>
            <Plus aria-hidden="true" className="h-3.5 w-3.5" />
            Add requirement
          </button>
        )}
      </div>
      {adding && editable && (
        <AddRequirementForm
          caseId={caseItem.id}
          onClose={() => setAdding(false)}
          onAdded={(link) => {
            setAdding(false)
            toast.success(`Linked ${link.requirement_key}`)
            changed()
          }}
        />
      )}
      {isLoading && !data ? (
        <div role="status" className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <LoadingSpinner size="sm" />
          {REQUIREMENTS_LOADING}
        </div>
      ) : error && !data ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-[var(--ui-danger-ink)]">
          <span>{REQUIREMENTS_ERROR}</span>
          <button type="button" className="btn-secondary btn-sm" onClick={() => void mutate()}>Retry</button>
        </div>
      ) : links.length === 0 ? (
        <p data-requirements-empty="" className="m-0 text-sm text-[var(--color-text-muted)]">{REQUIREMENTS_EMPTY}</p>
      ) : (
        <ul className="m-0 list-none space-y-1 p-0">
          {links.map((link) => (
            <Fragment key={link.id}>
              <li
                data-requirement-link={link.id}
                // A fixed key track: the titles line up row to row, and a key wider than it is cut (the
                // button's title carries it whole) instead of taking the title's room in a 399 px pane.
                className="grid grid-cols-[6.5rem_minmax(0,1fr)_auto_auto] items-center gap-2 rounded-md px-1 py-1 hover:bg-[var(--color-bg-hover)]"
              >
                <button
                  type="button"
                  className={KEY_BUTTON}
                  title={`Show the cases that link ${link.requirement_key}`}
                  onClick={() => onFilterRequirement(link.normalized_key)}
                >
                  {link.requirement_key}
                </button>
                <div className="min-w-0">
                  <p className="m-0 truncate text-sm text-[var(--color-text)]" title={link.requirement_title ?? undefined}>
                    {link.requirement_title || <span className="text-[var(--color-text-faint)]">—</span>}
                  </p>
                  <p className="m-0 truncate text-xs text-[var(--color-text-muted)]">{addedByLine(link)}</p>
                </div>
                <span className={TYPE_PILL} data-requirement-type={link.link_type}>{linkTypeLabel(link.link_type)}</span>
                {editable ? (
                  <button
                    type="button"
                    className={ICON_BUTTON}
                    aria-label={`Remove ${link.requirement_key}`}
                    title="Remove"
                    disabled={removing}
                    onClick={() => {
                      setRemoveError(null)
                      setConfirming(link.id)
                    }}
                  >
                    <X aria-hidden="true" className="h-4 w-4" />
                  </button>
                ) : (
                  <span />
                )}
              </li>
              {editable && confirming === link.id && (
                <li data-in-page-row="">
                  <InlineConfirm
                    message={<>Remove <strong className="break-words font-mono">{link.requirement_key}</strong> from this case?</>}
                    confirmLabel="Remove"
                    tone="danger"
                    busy={removing}
                    onConfirm={() => void remove(link)}
                    onCancel={() => setConfirming(null)}
                  />
                </li>
              )}
              {removeError?.id === link.id && (
                <li role="alert" className="text-sm text-[var(--ui-danger-ink)]">{removeError.text}</li>
              )}
            </Fragment>
          ))}
        </ul>
      )}
    </section>
  )
}
