/**
 * Java source roots (FAIL-08): where a project's JVM test sources live, so a
 * frame such as `at com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)` can
 * be located as `payments/src/test/java/com/acme/pay/CheckoutTest.java:42`.
 *
 * Shapes mirror `backend/app/models/schemas.py` (`SourceRoot`,
 * `SourceRootsResponse`, `SourceRootsPreview`) and the routes under
 * `/api/v1/projects/{project_id}/ownership/source-roots`.
 */

/** One root: an optional dotted package prefix ("" = every package) and a repo-relative directory. */
export interface SourceRoot {
  prefix: string
  root: string
}

export interface SourceRootsResponse {
  roots: SourceRoot[]
  max_roots: number
}

/** What `POST .../source-roots/preview` answers for an edited list: nothing is written. */
export interface SourceRootsPreview {
  /** Recent FAILED/BROKEN rows read (<= 500, last `lookback_days`). */
  sampled: number
  /** Of those, rows whose trace holds a Java frame. */
  java_frames: number
  /** Java rows the roots map to a repo path. */
  located: number
  /** Located rows a path rule matches. */
  matched: number
  lookback_days: number
  /** <= 5 located rows, rendered as text. */
  examples: { test_name: string; path: string }[]
}
