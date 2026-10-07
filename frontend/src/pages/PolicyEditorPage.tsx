/**
 * Release-gate policies: the list (`/policies`) and the editor
 * (`/policies/new`, `/policies/:policyId`).
 *
 * UX redesign P5 (items 6 and 4): the editor is the decision rules a release
 * is held to, so those stay open — metadata, the GO / NO_GO thresholds, the
 * pass-rate bands, the rules. The tuning behind them (hard caps, failure-kind
 * weighting, dimension weights) is in collapsed `Disclosure`s whose one-line
 * summary still states the current values, and the weights' validity, so a
 * save refused for an off-sum weight set says why without opening anything.
 * At >= 1280 px the simulator is a sticky right column: try the policy
 * against a run while editing it, without scrolling to the bottom and back.
 */
import { useState } from 'react';
import { useProjectStore } from '@/store/projectStore';
import { useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import { ArrowLeft, Plus } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import Disclosure from '@/components/ui/Disclosure';
import { helpTopicParam } from '@/components/help/helpTopics';
import {
  type PolicyDocument,
  type PolicyKindBudget,
  type PolicyRule,
  type RuleEvaluation,
  type SimulateResponse,
  DEFAULT_THRESHOLDS,
  DEFAULT_WEIGHTS,
  DEFAULT_PASS_RATE_BANDS,
  DEFAULT_HARD_CAPS,
  DEFAULT_KIND_RULES,
  createPolicy,
  deactivatePolicy,
  publishPolicy,
  simulatePolicy,
  updatePolicy,
} from '../services/policyService';
import { usePolicies, usePolicy } from '@/hooks/usePolicyEditor';

const RULE_TYPES = [
  { type: 'flaky_recurrence', label: 'Flaky Recurrence Limit', defaultParams: { max_flaky_tests: 10, action: 'BLOCK' } },
  { type: 'open_defect_limit', label: 'Open Defect Limit', defaultParams: { max_open_defects: 5, action: 'BLOCK' } },
  { type: 'dimension_ceiling', label: 'Dimension Ceiling', defaultParams: { dimension: 'regression_likely', max_score: 70, action: 'BLOCK' } },
  { type: 'override_rules', label: 'Override Restrictions', defaultParams: { allow_override_to_go_from_no_go: true, require_reason_min_length: 0 } },
];

const DIMENSIONS = [
  { key: 'user_impact', label: 'User Impact' },
  { key: 'env_sensitivity', label: 'Env Sensitivity' },
  { key: 'reproducibility', label: 'Reproducibility' },
  { key: 'regression_likely', label: 'Regression Likely' },
  { key: 'hist_recurrence', label: 'Hist. Recurrence' },
  { key: 'blast_radius', label: 'Blast Radius' },
  { key: 'diagnosis_conf', label: 'Diagnosis Conf' },
];

function emptyDocument(): PolicyDocument {
  return {
    schema_version: 1,
    thresholds: { ...DEFAULT_THRESHOLDS },
    dimension_weights: { ...DEFAULT_WEIGHTS },
    rules: [],
    pass_rate_bands: { ...DEFAULT_PASS_RATE_BANDS },
    hard_caps: { ...DEFAULT_HARD_CAPS },
    kind_rules: { ...DEFAULT_KIND_RULES },
  };
}

const EXCLUDABLE_KINDS = [
  { key: 'infrastructure', label: 'Infrastructure', hint: 'Environment, network, platform, runner failures' },
  { key: 'test_code', label: 'Test code', hint: 'Broken scripts, bad fixtures, flaky test-side races' },
] as const;

// The app's form look (`.input`, as on the release dialog), a label above
// each field, and a section per card.
const FIELD = 'input mt-1 disabled:opacity-70';
const FIELD_SM = 'input mt-0.5 px-2 py-1 text-xs disabled:opacity-70';
const LABEL = 'text-xs text-[var(--color-text-muted)]';
const LABEL_SM = 'text-[10px] text-[var(--color-text-muted)]';
const HINT = 'text-[10px] text-[var(--color-text-muted)] mt-0.5';
const SECTION = 'card space-y-3';
const SECTION_TITLE = 'text-sm font-semibold text-[var(--color-text)]';

const recommendationTone = (recommendation: string) => ({
  'text-[var(--status-passed)]': recommendation === 'GO',
  'text-[var(--status-broken)]': recommendation === 'CONDITIONAL_GO',
  'text-[var(--status-failed)]': recommendation === 'NO_GO',
});

/**
 * "What would this policy have said about run X?" — the document as edited
 * (saved or not) against a past run, beside the form at >= 1280 px.
 */
function PolicySimulator({ doc }: { doc: PolicyDocument }) {
  const [simRunId, setSimRunId] = useState('');
  const [simResult, setSimResult] = useState<SimulateResponse | null>(null);
  const [simulating, setSimulating] = useState(false);

  const handleSimulate = async () => {
    if (!simRunId.trim()) { toast.error('Enter a run ID'); return; }
    setSimulating(true);
    try {
      const result = await simulatePolicy(simRunId.trim(), doc);
      setSimResult(result);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Simulation failed');
    } finally {
      setSimulating(false);
    }
  };

  return (
    <section
      data-policy-simulator=""
      aria-labelledby="policy-simulator-title"
      className={clsx(SECTION, 'xl:sticky xl:top-4')}
    >
      <h2 id="policy-simulator-title" className={SECTION_TITLE}>Policy Simulator</h2>
      <p className="text-xs text-[var(--color-text-muted)]">Test this policy against a past test run to see how the decision would change.</p>
      <div>
        <label htmlFor="policy-sim-run" className={LABEL}>Run ID</label>
        <div className="mt-1 flex gap-2">
          <input id="policy-sim-run" placeholder="Run ID" value={simRunId} onChange={e => setSimRunId(e.target.value)}
            className="input min-w-0 flex-1" />
          <button onClick={handleSimulate} disabled={simulating}
            className="shrink-0 px-4 py-2 bg-[var(--status-flaky-bg)] text-[var(--color-text)] rounded-lg hover:bg-[var(--status-flaky-bg)] text-sm disabled:opacity-50">
            {simulating ? 'Simulating...' : 'Simulate'}
          </button>
        </div>
      </div>

      {simResult && (
        <div data-policy-simulation="" className="bg-[var(--color-bg)]/50 rounded-lg p-3 space-y-3">
          <div className="grid grid-cols-2 gap-3 text-center">
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">Original</p>
              <p className={clsx('text-base font-bold break-words', recommendationTone(simResult.original_recommendation))}>{simResult.original_recommendation}</p>
              <p className="text-xs text-[var(--color-text-muted)]">Composite: {simResult.original_composite.toFixed(1)}</p>
            </div>
            <div>
              <p className="text-xs text-[var(--color-text-muted)]">Simulated</p>
              <p className={clsx('text-base font-bold break-words', recommendationTone(simResult.simulated_recommendation))}>{simResult.simulated_recommendation}</p>
              <p className="text-xs text-[var(--color-text-muted)]">Composite: {simResult.simulated_composite.toFixed(1)}</p>
            </div>
          </div>
          <p className="text-sm text-[var(--color-text-secondary)]">{simResult.diff_summary}</p>
          {simResult.rule_evaluations.length > 0 && (
            <div className="space-y-1">
              {simResult.rule_evaluations.map((ev: RuleEvaluation) => (
                <div key={ev.rule_id} className="flex items-start gap-2 text-xs">
                  <span className={clsx('mt-1 w-1.5 h-1.5 shrink-0 rounded-full', ev.passed ? 'bg-[var(--status-passed-bg)]' : 'bg-[var(--status-failed-bg)]')} />
                  <span className="text-[var(--color-text-secondary)]">{ev.rule_name}</span>
                  <span className="text-[var(--color-text-muted)]">{ev.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default function PolicyEditorPage() {
  const { policyId } = useParams<{ policyId: string }>();
  const navigate = useNavigate();
  const projects = useProjectStore((state) => state.projects);
  /**
   * Name the project a policy governs, not its id.
   *
   * This list used to render `project_id.slice(0, 8)` — three policies for
   * three different projects, told apart only by comparing hex fragments, on
   * the one surface whose job is to say which thresholds apply where. The
   * names are already in the store; `/releases` renders the same relationship
   * correctly, so this was an inconsistency rather than a data limitation.
   *
   * Falls back to the truncated id when the lookup misses, which happens for a
   * policy on a project the signed-in user cannot see — better a short id than
   * a blank or a confident wrong name.
   */
  const projectLabel = (projectId: string): string =>
    projects.find((project) => project.id === projectId)?.name
    ?? `${projectId.slice(0, 8)}...`;
  const isNew = !policyId || policyId === 'new';

  // Editor state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [projectId, setProjectId] = useState<string | null>(null);
  const [doc, setDoc] = useState<PolicyDocument>(emptyDocument());
  const [isDraft, setIsDraft] = useState(true);
  const [saving, setSaving] = useState(false);
  // Tracks which policy id the editable form fields below were seeded from, so
  // the "adjust state during render" seeding (below) runs once per loaded
  // policy instead of from a set-state-in-effect.
  const [seededPolicyId, setSeededPolicyId] = useState<string | null>(null);

  // Data fetches are SWR hooks — each owns its loading/data/error state and
  // re-keys on the route params, so the page no longer drives state from a
  // load-on-mount effect. List mode fetches the policy list (and exposes
  // `refreshPolicies` so a deactivate can revalidate it); edit mode fetches the
  // single policy keyed on its id.
  const listMode = isNew && !policyId;
  const {
    policies,
    isLoading: policiesLoading,
    isError: policiesError,
    mutate: refreshPolicies,
  } = usePolicies(listMode);
  const {
    policy: loadedPolicy,
    isLoading: policyLoading,
    isError: policyError,
  } = usePolicy(policyId, !isNew);

  // Seed the editable form fields from the loaded policy during render (the
  // "adjust state during render" pattern) — once per loaded policy, tracked by
  // `seededPolicyId` — instead of from a set-state-in-effect. React re-renders
  // synchronously with the new state before painting, so the form shows the
  // loaded values without a flash.
  if (loadedPolicy && policyId && seededPolicyId !== policyId) {
    setSeededPolicyId(policyId);
    setName(loadedPolicy.name);
    setDescription(loadedPolicy.description || '');
    setProjectId(loadedPolicy.project_id);
    setDoc(loadedPolicy.rules as PolicyDocument);
    setIsDraft(loadedPolicy.is_draft);
  }

  // Surface load failures the way the old single-shot try/catch did.
  const loading = listMode ? policiesLoading : policyLoading;
  const displayError =
    (listMode && policiesError && 'Failed to load policies') ||
    (!isNew && policyError && 'Policy not found') ||
    null;

  const weightsSum = Object.values(doc.dimension_weights).reduce((s, v) => s + v, 0);
  const weightsValid = Math.abs(weightsSum - 1.0) <= 0.01;

  const handleSave = async () => {
    if (!name.trim()) { toast.error('Name is required'); return; }
    if (!weightsValid) { toast.error('Dimension weights must sum to 1.0'); return; }
    if (doc.thresholds.go_threshold >= doc.thresholds.no_go_threshold) {
      toast.error('GO threshold must be less than NO_GO threshold');
      return;
    }
    const bands = doc.pass_rate_bands ?? DEFAULT_PASS_RATE_BANDS;
    if (!(bands.orange_min < bands.yellow_min && bands.yellow_min < bands.green_min)) {
      toast.error('Pass-rate bands must be strictly increasing (orange < yellow < green)');
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        const created = await createPolicy({ project_id: projectId, name, description, rules: doc });
        toast.success('Policy created');
        navigate(`/policies/${created.id}`, { replace: true });
      } else if (policyId) {
        await updatePolicy(policyId, { name, description, rules: doc });
        toast.success('Policy saved');
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handlePublish = async () => {
    if (!policyId) return;
    if (!confirm('Publish this policy? It will become active and the previous version will be deactivated.')) return;
    try {
      await publishPolicy(policyId);
      toast.success('Policy published and activated');
      setIsDraft(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Publish failed');
    }
  };

  const handleDeactivate = async (id: string) => {
    if (!confirm('Deactivate this policy?')) return;
    try {
      await deactivatePolicy(id);
      toast.success('Policy deactivated');
      refreshPolicies();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed');
    }
  };

  const addRule = (type: string) => {
    const tmpl = RULE_TYPES.find(r => r.type === type);
    if (!tmpl) return;
    const newRule: PolicyRule = {
      id: `${type}-${Date.now()}`,
      name: tmpl.label,
      type,
      enabled: true,
      params: { ...tmpl.defaultParams },
    };
    setDoc(d => ({ ...d, rules: [...d.rules, newRule] }));
  };

  const removeRule = (idx: number) => {
    setDoc(d => ({ ...d, rules: d.rules.filter((_, i) => i !== idx) }));
  };

  const updateRule = (idx: number, updates: Partial<PolicyRule>) => {
    setDoc(d => ({
      ...d,
      rules: d.rules.map((r, i) => i === idx ? { ...r, ...updates } : r),
    }));
  };

  const updateRuleParam = (idx: number, key: string, value: unknown) => {
    setDoc(d => ({
      ...d,
      rules: d.rules.map((r, i) => i === idx ? { ...r, params: { ...r.params, [key]: value } } : r),
    }));
  };

  // ── List mode ─────────────────────────────────────────────────────────────

  if (isNew && !policyId) {
    return (
      <div className="space-y-4">
        <PageHeader
          compact
          title="Release Gate Policies"
          subtitle="Define per-project release gate thresholds, rules, and override constraints."
          helpTopic={helpTopicParam('/policies')}
          actions={
            <button onClick={() => navigate('/policies/new')} className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-md hover:bg-[var(--color-btn-primary-hover)] text-sm font-medium">
              <Plus className="h-4 w-4" /> New Policy
            </button>
          }
        />

        {displayError && <div className="bg-[var(--status-failed-bg)]/30 border border-[var(--status-failed-bd)] rounded-lg p-3 text-[var(--status-failed)] text-sm">{displayError}</div>}

        {loading ? (
          <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div>
        ) : policies.length === 0 ? (
          <div className="text-center py-12 text-[var(--color-text-muted)]">
            No policies configured. Create one to customize release gate thresholds per project.
          </div>
        ) : (
          <div data-primary="" className="space-y-2">
            {policies.map(p => (
              <div key={p.id} className="card flex justify-between items-center">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[var(--color-text)] font-medium">{p.name}</span>
                    <span className="text-[var(--color-text-muted)] text-xs">v{p.version}</span>
                    <span className={clsx('px-2 py-0.5 rounded text-xs', {
                      'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]': p.is_active,
                      'bg-[var(--status-skipped-bg)]/40 text-[var(--status-skipped)]': p.is_draft && !p.is_active,
                      'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]': !p.is_draft && !p.is_active,
                    })}>
                      {p.is_active ? 'Active' : p.is_draft ? 'Draft' : 'Inactive'}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--color-text-muted)] mt-1">
                    {p.project_id ? `Project: ${projectLabel(p.project_id)}` : 'System Default'}
                    {' · '}Created: {new Date(p.created_at).toLocaleDateString()}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => navigate(`/policies/${p.id}`)} className="px-3 py-1 text-xs bg-[var(--color-bg-secondary)] rounded hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">
                    {p.is_draft ? 'Edit' : 'View'}
                  </button>
                  {p.is_active && (
                    <button onClick={() => handleDeactivate(p.id)} className="px-3 py-1 text-xs bg-[var(--status-failed-bg)]/50 rounded hover:bg-[var(--status-failed-bg)] text-[var(--status-failed)]">
                      Deactivate
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── Editor mode ───────────────────────────────────────────────────────────

  if (loading) return <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div>;

  const bands = doc.pass_rate_bands ?? DEFAULT_PASS_RATE_BANDS;
  const bandsValid = bands.orange_min < bands.yellow_min && bands.yellow_min < bands.green_min;
  const caps = doc.hard_caps ?? DEFAULT_HARD_CAPS;
  const kindRules = doc.kind_rules ?? DEFAULT_KIND_RULES;
  const kindBudgets = EXCLUDABLE_KINDS.filter(({ key }) => kindRules[key] != null).map(({ label }) => label.toLowerCase());
  const scope = projectId ? `Project: ${projectLabel(projectId)}` : 'System default';
  const state = isNew ? 'New draft' : isDraft ? 'Draft' : 'Published (read-only)';
  const version = loadedPolicy && !isNew ? ` · v${loadedPolicy.version}` : '';

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title={isNew ? 'New Policy' : `Edit: ${name}`}
        subtitle={`${state}${version} · ${scope}`}
        helpTopic={helpTopicParam('/policies')}
        overflow={[{ label: 'Back to policies', href: '/policies', icon: <ArrowLeft className="h-4 w-4" /> }]}
        actions={isDraft ? (
          <>
            <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-lg hover:bg-[var(--color-btn-primary-hover)] text-sm disabled:opacity-50">
              {saving ? 'Saving...' : 'Save Draft'}
            </button>
            {!isNew && (
              <button onClick={handlePublish} className="px-4 py-2 bg-[var(--status-passed-bg)] text-[var(--color-text)] rounded-lg hover:bg-[var(--status-passed-bg)] text-sm">
                Publish
              </button>
            )}
          </>
        ) : undefined}
      />

      {displayError && <div className="bg-[var(--status-failed-bg)]/30 border border-[var(--status-failed-bd)] rounded-lg p-3 text-[var(--status-failed)] text-sm">{displayError}</div>}

      <div data-policy-layout="" className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
        <div data-primary="" data-policy-form="" className="min-w-0 space-y-4">
          {/* Metadata — a label/field grid: name and project side by side, the description full width. */}
          <section className={SECTION}>
            <h2 className={SECTION_TITLE}>Metadata</h2>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="policy-name" className={LABEL}>Name</label>
                <input id="policy-name" placeholder="Policy Name *" value={name} onChange={e => setName(e.target.value)}
                  className={FIELD} disabled={!isDraft} />
              </div>
              <div>
                <label htmlFor="policy-project" className={LABEL}>Project ID</label>
                <input id="policy-project" placeholder="Project ID (empty = system default)" value={projectId || ''}
                  onChange={e => setProjectId(e.target.value || null)}
                  className={FIELD} disabled={!isDraft} />
                <p className={HINT}>{scope}</p>
              </div>
            </div>
            <div>
              <label htmlFor="policy-description" className={LABEL}>Description</label>
              <textarea id="policy-description" placeholder="Description" value={description} onChange={e => setDescription(e.target.value)}
                className={clsx(FIELD, 'h-16 resize-y')} disabled={!isDraft} />
            </div>
          </section>

          {/* Thresholds */}
          <section className={SECTION}>
            <h2 className={SECTION_TITLE}>Thresholds</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {([
                ['go_threshold', 'GO Threshold', 'Composite below this → GO'],
                ['no_go_threshold', 'NO_GO Threshold', 'Composite at or above this → NO_GO'],
                ['pass_rate_minimum', 'Pass Rate Minimum', 'Target pass rate (%)'],
                ['pass_rate_hard_floor_factor', 'Hard Floor Factor', 'Factor × min pass rate → force NO_GO'],
              ] as const).map(([key, label, hint]) => (
                <div key={key}>
                  <label htmlFor={`policy-threshold-${key}`} className={LABEL}>{label}</label>
                  <input id={`policy-threshold-${key}`} type="number" step="0.1"
                    value={doc.thresholds[key as keyof typeof doc.thresholds]}
                    onChange={e => setDoc(d => ({ ...d, thresholds: { ...d.thresholds, [key]: parseFloat(e.target.value) || 0 } }))}
                    className={FIELD} disabled={!isDraft} />
                  <p className={HINT}>{hint}</p>
                </div>
              ))}
            </div>
          </section>

          {/* Pass-Rate Bands (4-colour build verdict) */}
          <section className={SECTION}>
            <div className="flex justify-between items-center">
              <h2 className={SECTION_TITLE}>Pass-Rate Bands</h2>
              <span className={clsx('text-xs', bandsValid ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]')}>
                {bandsValid ? 'Bands valid' : 'Bands must be strictly increasing (orange < yellow < green)'}
              </span>
            </div>
            <p className="text-[11px] text-[var(--color-text-muted)]">
              Build colour by pass rate. Verdict: green/yellow → GO · orange → CONDITIONAL · red → NO-GO.
              Hard caps can downgrade the band one step at a time.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {([
                ['orange_min', 'Orange ≥ (%)', 'Below this → red'],
                ['yellow_min', 'Yellow ≥ (%)', 'Range [orange, yellow) → orange'],
                ['green_min',  'Green ≥ (%)',  'Range [yellow, green) → yellow; ≥ this → green'],
              ] as const).map(([key, label, hint]) => (
                <div key={key}>
                  <label htmlFor={`policy-band-${key}`} className={LABEL}>{label}</label>
                  <input id={`policy-band-${key}`} type="number" step="0.1" min={0} max={100}
                    value={bands[key]}
                    onChange={e => setDoc(d => ({
                      ...d,
                      pass_rate_bands: {
                        ...(d.pass_rate_bands ?? DEFAULT_PASS_RATE_BANDS),
                        [key]: parseFloat(e.target.value) || 0,
                      },
                    }))}
                    className={FIELD}
                    disabled={!isDraft} />
                  <p className={HINT}>{hint}</p>
                </div>
              ))}
            </div>
          </section>

          {/* Rules */}
          <section className={SECTION}>
            <div className="flex justify-between items-center">
              <h2 className={SECTION_TITLE}>Rules</h2>
              {isDraft && (
                <select aria-label="Add rule" onChange={e => { if (e.target.value) { addRule(e.target.value); e.target.value = ''; } }}
                  className="input w-auto px-3 py-1.5 text-xs" defaultValue="">
                  <option value="" disabled>+ Add Rule</option>
                  {RULE_TYPES.map(t => <option key={t.type} value={t.type}>{t.label}</option>)}
                </select>
              )}
            </div>

            {doc.rules.length === 0 && <p className="text-[var(--color-text-muted)] text-sm">No rules configured. The policy will use thresholds only.</p>}

            {doc.rules.map((rule, idx) => (
              <div key={rule.id} data-policy-rule={rule.type} className="bg-[var(--color-bg-secondary)] rounded-lg p-3 space-y-2">
                <div className="flex justify-between items-center">
                  <div className="flex items-center gap-2">
                    <input type="checkbox" aria-label={`${rule.name} enabled`} checked={rule.enabled}
                      onChange={e => updateRule(idx, { enabled: e.target.checked })} disabled={!isDraft} />
                    <input aria-label="Rule name" value={rule.name} onChange={e => updateRule(idx, { name: e.target.value })}
                      className="bg-transparent text-[var(--color-text)] text-sm font-medium border-none outline-none" disabled={!isDraft} />
                    <span className="text-xs text-[var(--color-text-muted)] font-mono">{rule.type}</span>
                  </div>
                  {isDraft && (
                    <button onClick={() => removeRule(idx)} className="text-xs text-[var(--status-failed)] hover:text-[var(--status-failed)]">Remove</button>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {rule.type === 'flaky_recurrence' && (
                    <>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-max-flaky`} className={LABEL_SM}>Max Flaky Tests</label>
                        <input id={`policy-rule-${idx}-max-flaky`} type="number" value={rule.params.max_flaky_tests as number ?? 10}
                          onChange={e => updateRuleParam(idx, 'max_flaky_tests', parseInt(e.target.value) || 0)}
                          className={FIELD_SM} disabled={!isDraft} />
                      </div>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-action`} className={LABEL_SM}>Action</label>
                        <select id={`policy-rule-${idx}-action`} value={rule.params.action as string ?? 'BLOCK'}
                          onChange={e => updateRuleParam(idx, 'action', e.target.value)}
                          className={FIELD_SM} disabled={!isDraft}>
                          <option value="BLOCK">BLOCK</option>
                          <option value="WARN">WARN</option>
                          <option value="INFO">INFO</option>
                        </select>
                      </div>
                    </>
                  )}
                  {rule.type === 'open_defect_limit' && (
                    <>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-max-defects`} className={LABEL_SM}>Max Open Defects</label>
                        <input id={`policy-rule-${idx}-max-defects`} type="number" value={rule.params.max_open_defects as number ?? 5}
                          onChange={e => updateRuleParam(idx, 'max_open_defects', parseInt(e.target.value) || 0)}
                          className={FIELD_SM} disabled={!isDraft} />
                      </div>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-action`} className={LABEL_SM}>Action</label>
                        <select id={`policy-rule-${idx}-action`} value={rule.params.action as string ?? 'BLOCK'}
                          onChange={e => updateRuleParam(idx, 'action', e.target.value)}
                          className={FIELD_SM} disabled={!isDraft}>
                          <option value="BLOCK">BLOCK</option>
                          <option value="WARN">WARN</option>
                          <option value="INFO">INFO</option>
                        </select>
                      </div>
                    </>
                  )}
                  {rule.type === 'dimension_ceiling' && (
                    <>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-dimension`} className={LABEL_SM}>Dimension</label>
                        <select id={`policy-rule-${idx}-dimension`} value={rule.params.dimension as string ?? 'regression_likely'}
                          onChange={e => updateRuleParam(idx, 'dimension', e.target.value)}
                          className={FIELD_SM} disabled={!isDraft}>
                          {DIMENSIONS.map(d => <option key={d.key} value={d.key}>{d.label}</option>)}
                        </select>
                      </div>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-max-score`} className={LABEL_SM}>Max Score</label>
                        <input id={`policy-rule-${idx}-max-score`} type="number" value={rule.params.max_score as number ?? 70}
                          onChange={e => updateRuleParam(idx, 'max_score', parseFloat(e.target.value) || 0)}
                          className={FIELD_SM} disabled={!isDraft} />
                      </div>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-action`} className={LABEL_SM}>Action</label>
                        <select id={`policy-rule-${idx}-action`} value={rule.params.action as string ?? 'BLOCK'}
                          onChange={e => updateRuleParam(idx, 'action', e.target.value)}
                          className={FIELD_SM} disabled={!isDraft}>
                          <option value="BLOCK">BLOCK</option>
                          <option value="WARN">WARN</option>
                        </select>
                      </div>
                    </>
                  )}
                  {rule.type === 'override_rules' && (
                    <>
                      <div className="flex items-center gap-2">
                        <input id={`policy-rule-${idx}-allow-override`} type="checkbox" checked={rule.params.allow_override_to_go_from_no_go as boolean ?? true}
                          onChange={e => updateRuleParam(idx, 'allow_override_to_go_from_no_go', e.target.checked)} disabled={!isDraft} />
                        <label htmlFor={`policy-rule-${idx}-allow-override`} className={LABEL_SM}>Allow NO_GO → GO override</label>
                      </div>
                      <div>
                        <label htmlFor={`policy-rule-${idx}-min-reason`} className={LABEL_SM}>Min Reason Length</label>
                        <input id={`policy-rule-${idx}-min-reason`} type="number" value={rule.params.require_reason_min_length as number ?? 0}
                          onChange={e => updateRuleParam(idx, 'require_reason_min_length', parseInt(e.target.value) || 0)}
                          className={FIELD_SM} disabled={!isDraft} />
                      </div>
                    </>
                  )}
                </div>
              </div>
            ))}
          </section>

          {/* Advanced: the tuning behind the rules above, collapsed. Each
              summary states the current values so a closed section still
              reads true, and the weights' sum (a save-time check) says when
              it is off. */}
          <Disclosure
            title="Hard caps"
            summary={`P0 defects ≤ ${caps.max_p0_defects} · flaky ≤ ${caps.max_flaky_count} · new failures (24h) ≤ ${caps.max_new_failures_24h}`}
          >
            <div className="space-y-3">
              <p className="text-[11px] text-[var(--color-text-muted)]">
                Each cap downgrades the resolved band one step when exceeded (green → yellow → orange → red).
                Set to 0 to disable a cap, except the P0 cap where 0 means "no P0 defects allowed".
              </p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {([
                  ['max_p0_defects', 'Max P0 defects', 'Active OPEN defects with severity=P0'],
                  ['max_flaky_count', 'Max flaky tests', 'Flaky-pattern tests in 10-run window'],
                  ['max_new_failures_24h', 'Max new failures (24h)', 'Failures created in the last 24h'],
                ] as const).map(([key, label, hint]) => (
                  <div key={key}>
                    <label htmlFor={`policy-cap-${key}`} className={LABEL}>{label}</label>
                    <input id={`policy-cap-${key}`} type="number" step="1" min={0}
                      value={caps[key]}
                      onChange={e => setDoc(d => ({
                        ...d,
                        hard_caps: {
                          ...(d.hard_caps ?? DEFAULT_HARD_CAPS),
                          [key]: parseInt(e.target.value, 10) || 0,
                        },
                      }))}
                      className={FIELD}
                      disabled={!isDraft} />
                    <p className={HINT}>{hint}</p>
                  </div>
                ))}
              </div>
            </div>
          </Disclosure>

          {/* Failure-Kind Weighting (opt-in, US-9.3) */}
          <Disclosure
            title="Failure-kind weighting"
            summary={kindRules.enabled
              ? `On${kindBudgets.length > 0 ? ` · budgets: ${kindBudgets.join(', ')}` : ' · no budgets'}`
              : 'Off (opt-in)'}
          >
            <div className="space-y-3">
              <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                <input
                  type="checkbox"
                  checked={kindRules.enabled ?? false}
                  onChange={e => setDoc(d => ({
                    ...d,
                    kind_rules: { ...(d.kind_rules ?? DEFAULT_KIND_RULES), enabled: e.target.checked },
                  }))}
                  disabled={!isDraft}
                />
                Enabled (opt-in)
              </label>
              <p className="text-[11px] text-[var(--color-text-muted)]">
                Weight failure kinds differently in the gate verdict. Failure kinds are AI-classified
                (derived from the failure-category classifier) — not ground truth. Infrastructure or
                test-code failures within their budget are excluded from the NO-GO trigger but always
                reported; exceeding a budget restores full counting. Product failures always count, and
                unknown-kind failures count as product. A NO-GO can be downgraded at most to
                CONDITIONAL-GO — never to GO. Disabled means verdicts are computed exactly as before.
              </p>
              {(kindRules.enabled ?? false) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {EXCLUDABLE_KINDS.map(({ key, label, hint }) => {
                    const budget = kindRules[key] ?? null;
                    const setBudget = (next: PolicyKindBudget | null) => setDoc(d => ({
                      ...d,
                      kind_rules: { ...(d.kind_rules ?? DEFAULT_KIND_RULES), enabled: true, [key]: next },
                    }));
                    return (
                      <div key={key} className="bg-[var(--color-bg-secondary)] rounded-lg p-3 space-y-2">
                        <div className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            aria-label={`${label} budget`}
                            checked={budget != null}
                            onChange={e => setBudget(e.target.checked ? { max_failures: 5, downgrade_to: 'CONDITIONAL_GO' } : null)}
                            disabled={!isDraft}
                          />
                          <span className="text-sm text-[var(--color-text)] font-medium">{label} budget</span>
                        </div>
                        <p className="text-[10px] text-[var(--color-text-muted)]">{hint}. No budget = failures of this kind count in full.</p>
                        {budget != null && (
                          <div className="grid grid-cols-2 gap-2">
                            <div>
                              <label htmlFor={`kind-max-${key}`} className={LABEL_SM}>Max failures excused</label>
                              <input id={`kind-max-${key}`} type="number" step="1" min={0} value={budget.max_failures}
                                onChange={e => setBudget({ ...budget, max_failures: Math.max(0, parseInt(e.target.value, 10) || 0) })}
                                className={FIELD_SM} disabled={!isDraft} />
                            </div>
                            <div>
                              <label htmlFor={`kind-downgrade-${key}`} className={LABEL_SM}>Downgrade NO-GO to</label>
                              <select id={`kind-downgrade-${key}`} value={budget.downgrade_to}
                                onChange={() => setBudget({ ...budget, downgrade_to: 'CONDITIONAL_GO' })}
                                className={FIELD_SM} disabled={!isDraft}>
                                <option value="CONDITIONAL_GO">CONDITIONAL_GO</option>
                              </select>
                              <p className={HINT}>Never GO — hard rule.</p>
                            </div>
                            <div className="col-span-2">
                              <label className={LABEL_SM} htmlFor={`kind-floor-${key}`}>
                                Min confidence to excuse (optional, 0-100)
                              </label>
                              <input
                                id={`kind-floor-${key}`}
                                type="number" step="1" min={0} max={100}
                                placeholder="No floor"
                                value={budget.min_confidence_to_excuse ?? ''}
                                onChange={e => {
                                  const raw = e.target.value.trim();
                                  setBudget({
                                    ...budget,
                                    min_confidence_to_excuse: raw === ''
                                      ? null
                                      : Math.max(0, Math.min(100, parseInt(raw, 10) || 0)),
                                  });
                                }}
                                className={FIELD_SM} disabled={!isDraft} />
                              <p className={HINT}>
                                Only failures whose AI kind confidence meets the floor are excusable;
                                below-floor failures count as product. Empty = no floor (behavior unchanged).
                              </p>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </Disclosure>

          {/* Dimension Weights */}
          <Disclosure
            title="Dimension weights"
            summary={
              <span data-weights-sum="" className={weightsValid ? undefined : 'text-[var(--status-failed)]'}>
                Sum: {weightsSum.toFixed(2)}{weightsValid ? '' : ' (must be 1.00)'}
              </span>
            }
          >
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {DIMENSIONS.map(({ key, label }) => (
                <div key={key}>
                  <label htmlFor={`policy-weight-${key}`} className={LABEL}>{label}</label>
                  <input id={`policy-weight-${key}`} type="number" step="0.01" min="0" max="1"
                    value={doc.dimension_weights[key as keyof typeof doc.dimension_weights]}
                    onChange={e => setDoc(d => ({
                      ...d, dimension_weights: { ...d.dimension_weights, [key]: parseFloat(e.target.value) || 0 },
                    }))}
                    className={FIELD} disabled={!isDraft} />
                </div>
              ))}
            </div>
          </Disclosure>
        </div>

        <PolicySimulator doc={doc} />
      </div>
    </div>
  );
}
