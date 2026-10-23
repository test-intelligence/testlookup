/**
 * /projects, the Integrations block of Edit Project and New Project (W10 INT-03
 * / INT-03b). Pure: the page and its tests import the same strings.
 *
 * D22: "Jenkins Job Pattern" is gone from the form (nothing read it; the stored
 * value and the API field stay). The two fields that remain each say what they
 * drive, and the server's 422 sentence for either shows under it.
 */

/** Under Splunk Index (`id="ep-splunk-help"`). */
export const SPLUNK_INDEX_HELP = "Used by AI log lookups for this project. Empty: the deployment's index."
/** Under OCP Namespace (`id="ep-ocp-help"`). */
export const OCP_NAMESPACE_HELP = "Used when a run names a pod but no namespace. Empty: the deployment's default namespace."

/** The Edit Project fields a field-level 422 may name (rendered under each). */
export const EDIT_PROJECT_ERROR_FIELDS = ['splunk_index', 'ocp_namespace', 'name', 'description', 'jira_project_key'] as const
/** The New Project fields a field-level 422 may name. */
export const NEW_PROJECT_ERROR_FIELDS = ['name', 'slug', 'description', 'jira_project_key', 'ocp_namespace'] as const

/**
 * The two fields the server validates with fixed sentences (W10 G-A): a PUT that
 * carries either asks for no global toast, so its 422 shows only under the field.
 */
export const VALIDATED_INTEGRATION_FIELDS = ['splunk_index', 'ocp_namespace'] as const

/** Whether a PUT body carries a field whose 422 the form renders itself. */
export function carriesValidatedIntegrationField(updates: object): boolean {
  return VALIDATED_INTEGRATION_FIELDS.some(field => Object.prototype.hasOwnProperty.call(updates, field))
}

/** The error line's text for a failure that is not a field-level 422: the server's string detail, else the fallback. */
export function saveFailureMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return typeof detail === 'string' && detail.trim() ? detail : fallback
}
