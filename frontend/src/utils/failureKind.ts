/**
 * Failure-kind triad (PMF US-9.1/US-9.2) — frontend mirror of
 * ``backend/app/services/failure_kind.py``. Keep the two in sync.
 *
 * Every analyzed failure is bucketed into product / test_code /
 * infrastructure (/ unknown). Kinds are AI-derived classifications —
 * always present them with the "AI-classified" provenance copy, never
 * as ground truth.
 *
 * Colors reference existing palette tokens only (declared as --kind-*
 * aliases in index.css) — no new hex values.
 */

export type FailureKind = 'product' | 'test_code' | 'infrastructure' | 'unknown'

export interface FailureKindDef {
  id: FailureKind
  label: string
  /** CSS variable from the existing palette system. */
  color: string
  /** One-line triage hint shown in tooltips. */
  desc: string
}

/** Fixed presentation order — matches the backend's FAILURE_KINDS. */
export const FAILURE_KIND_DEFS: FailureKindDef[] = [
  { id: 'product',        label: 'Product',        color: 'var(--kind-product)',   desc: 'Regression in the system under test — route to a developer.' },
  { id: 'test_code',      label: 'Test code',      color: 'var(--kind-test-code)', desc: 'Broken script, bad fixture, or flaky test — test-suite work.' },
  { id: 'infrastructure', label: 'Infrastructure', color: 'var(--kind-infra)',     desc: 'Environment / network / runner failure — not a code problem.' },
  { id: 'unknown',        label: 'Unknown',        color: 'var(--kind-unknown)',   desc: 'Classifier could not decide — needs manual review.' },
]

export function kindDef(kind: string | null | undefined): FailureKindDef {
  return FAILURE_KIND_DEFS.find(k => k.id === kind) ?? FAILURE_KIND_DEFS[3]
}

/**
 * Category → kind mapping, mirroring the backend's KIND_BY_CATEGORY.
 * Used as a fallback when a payload row carries a category but no
 * server-derived kind (older cached responses).
 */
const KIND_BY_CATEGORY: Record<string, FailureKind> = {
  PRODUCT_BUG: 'product',
  INFRASTRUCTURE: 'infrastructure',
  TEST_DATA: 'test_code',
  AUTOMATION_DEFECT: 'test_code',
  FLAKY: 'test_code',
  UNKNOWN: 'unknown',
}

export function failureKindOf(category: string | null | undefined): FailureKind {
  const raw = (category ?? '').trim().toUpperCase()
  return KIND_BY_CATEGORY[raw] ?? 'unknown'
}
