import { ExternalLink } from 'lucide-react'
import { isSafeExternalUrl } from '@/utils/safeUrl'
import type { NormalizedTestCaseDetail } from '@/types/test-case-detail'

function Value({ value }: { value?: string | null }) {
  return <span className="truncate text-right text-xs text-[var(--color-text-secondary)]">{value || 'Not supplied'}</span>
}

function Row({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</span>
      <Value value={value} />
    </div>
  )
}

function SectionTitle({ children }: { children: string }) {
  return <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{children}</p>
}

export default function TestCaseDetailSummary({ detail }: { detail: NormalizedTestCaseDetail }) {
  const identity = detail.identity
  const classification = detail.classification
  const definition = detail.definition
  const provenance = detail.provenance
  const links = (detail.links ?? []).filter(link => isSafeExternalUrl(link.url))
  const components = classification?.components ?? []
  const tags = classification?.tags ?? []
  const hasDefinition = Boolean(definition && Object.values(definition).some(value => value != null && (!Array.isArray(value) || value.length > 0)))

  return (
    <section className="card space-y-3 p-3" aria-label="Enriched test case details">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Test case detail</h2>
        <span className="badge bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
          {detail.source === 'enriched' && detail.contract
            ? `${detail.contract} v${detail.schema_version ?? 'unknown'}`
            : 'legacy run response'}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <SectionTitle>Identity</SectionTitle>
          <Row label="Logical test ID" value={detail.canonical_test_case_id || identity?.test_case_id} />
          <Row label="History ID" value={identity?.history_id} />
          <Row label="Full name" value={identity?.full_name} />
          <Row label="Fingerprint" value={identity?.fingerprint} />
        </div>

        <div>
          <SectionTitle>Classification</SectionTitle>
          <Row label="Suite" value={classification?.suite?.legacy_name || classification?.suite?.name} />
          <Row label="Class" value={classification?.class_name} />
          <Row label="Package / module" value={classification?.package_or_module} />
          <Row label="Framework" value={classification?.framework} />
          <Row label="Service" value={classification?.service ? `${classification.service.name}${classification.service.source ? ` · ${classification.service.source}` : ''}` : null} />
          {components.length > 0 && (
            <div className="flex items-baseline justify-between gap-3 py-1">
              <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Components</span>
              <span className="flex flex-wrap justify-end gap-1">
                {components.map(component => <span key={`${component.name}-${component.source ?? ''}`} className="badge">{component.name}</span>)}
              </span>
            </div>
          )}
        </div>
      </div>

      {(tags.length > 0 || (classification?.labels?.length ?? 0) > 0) && (
        <div className="border-t border-[var(--color-border)] pt-2">
          <SectionTitle>Labels and tags</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {tags.map(tag => <span key={tag} className="badge">{tag}</span>)}
            {(classification?.labels ?? []).map(label => <span key={`${label.name}-${label.value}`} className="badge">{label.name}: {label.value}</span>)}
          </div>
        </div>
      )}

      {hasDefinition && (
        <div className="border-t border-[var(--color-border)] pt-2">
          <SectionTitle>Definition</SectionTitle>
          <div className="space-y-1 text-xs text-[var(--color-text-secondary)]">
            {definition?.description && <p><strong>Description:</strong> {definition.description}</p>}
            {definition?.objective && <p><strong>Objective:</strong> {definition.objective}</p>}
            {definition?.preconditions && <p><strong>Preconditions:</strong> {definition.preconditions}</p>}
            {definition?.expected_result && <p><strong>Expected result:</strong> {definition.expected_result}</p>}
            {definition?.test_data && <p><strong>Test data:</strong> {definition.test_data}</p>}
            {(definition?.steps?.length ?? 0) > 0 && (
              <div>
                <strong>Authored steps:</strong>
                <ol className="ml-5 list-decimal">
                  {definition?.steps?.map(step => (
                    <li key={`${step.step_number}-${step.action}`}>{step.action}{step.expected_result ? ` — ${step.expected_result}` : ''}</li>
                  ))}
                </ol>
              </div>
            )}
            {(definition?.parameters?.length ?? 0) > 0 && (
              <div><strong>Authored parameters:</strong> {definition?.parameters?.map(parameter => parameter.name).join(', ')}</div>
            )}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 border-t border-[var(--color-border)] pt-2 md:grid-cols-2">
        <div>
          <SectionTitle>Source and provenance</SectionTitle>
          <Row label="Format" value={provenance?.format || classification?.framework} />
          <Row label="Source file" value={provenance?.source_file} />
          <Row label="Parser version" value={provenance?.parser_version} />
          {!provenance && <p className="text-xs text-[var(--color-text-muted)]">Provenance was not supplied by the response.</p>}
        </div>
        <div>
          <SectionTitle>Links</SectionTitle>
          {links.length === 0 ? (
            <p className="text-xs text-[var(--color-text-muted)]">No safe links supplied.</p>
          ) : (
            <ul className="space-y-1">
              {links.map((link, index) => (
                <li key={`${link.url}-${index}`}>
                  <a href={link.url} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 text-xs text-[var(--color-accent)] hover:underline">
                    <span className="truncate">{link.name || link.url}</span><ExternalLink className="h-3 w-3 shrink-0" />
                  </a>
                  {link.type && <span className="ml-1 text-[10px] text-[var(--color-text-muted)]">{link.type}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {(provenance?.warnings?.length ?? 0) > 0 && (
        <p className="border-t border-[var(--color-border)] pt-2 text-xs text-[var(--color-text-muted)]">
          {provenance?.warnings?.length} source warning{provenance?.warnings?.length === 1 ? '' : 's'} recorded; some optional details may be unavailable.
        </p>
      )}
    </section>
  )
}
