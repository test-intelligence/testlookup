/**
 * FAIL-10 project classification hints
 * (`/api/v1/projects/{project_id}/classification-hints`).
 *
 * A hint is "<field> contains <text> -> <category>": a case-insensitive
 * substring rule a QA lead sets so new failures that match are classified
 * before any engine runs. Vocabularies (`match_field`, `category`) are plain
 * strings, validated by the server; the labels live in
 * `components/failures/ClassificationHintsModal.tsx`.
 */

export interface ClassificationHint {
  id: string
  project_id: string
  match_field: string
  pattern: string
  category: string
  note: string | null
  enabled: boolean
  created_by_name: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ClassificationHintList {
  /** `created_at, id` order = precedence order (the oldest matching hint wins). */
  items: ClassificationHint[]
  total: number
  max_hints: number
}

/** POST body (every field but `note` / `enabled` required) and PATCH body (only the fields sent apply). */
export interface ClassificationHintBody {
  match_field?: string
  pattern?: string
  category?: string
  note?: string | null
  enabled?: boolean
}

export interface ClassificationHintPreview {
  matched: number
  sampled: number
  lookback_days: number
  examples: { test_name: string; value: string }[]
}
