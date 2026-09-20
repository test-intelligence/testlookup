import { useState } from 'react';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import {
  type OwnershipRule,
  CODEOWNERS_SERVICE,
  MATCH_TYPES,
  createOwnershipRule,
  deleteOwnershipRule,
  deleteTeamChannel,
  importCodeowners,
  updateOwnershipRule,
  upsertTeamChannel,
} from '../services/ownershipService';
import { useProjectStore, ALL_PROJECTS_ID } from '../store/projectStore';
import { useCodeownersCoverage, useOwnershipRules } from '../hooks/useOwnershipRules';
import { useTeamChannels } from '../hooks/useTeamChannels';

type ChannelType = 'email' | 'slack' | 'teams';

const CHANNEL_TYPES: Array<{ value: ChannelType; label: string }> = [
  { value: 'slack', label: 'Slack webhook' },
  { value: 'teams', label: 'Teams webhook' },
  { value: 'email', label: 'Email' },
];

export default function OwnershipEditorPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId);
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId;

  const { rules, isLoading: loading, isError, refresh } = useOwnershipRules(projectId);
  const { coverage, refresh: refreshCoverage } = useCodeownersCoverage(projectId);
  const {
    channels,
    isError: isChannelsError,
    refresh: refreshChannels,
  } = useTeamChannels(projectId);

  // CODEOWNERS import dialog (US-8.3)
  const [showImport, setShowImport] = useState(false);

  // Team-channel edit buffers (US-7.3), keyed by team name
  const [channelEdits, setChannelEdits] = useState<
    Record<string, { channel_type: ChannelType; target: string }>
  >({});

  // New rule form
  const [showForm, setShowForm] = useState(false);
  const [matchType, setMatchType] = useState('suite_name');
  const [matchPattern, setMatchPattern] = useState('');
  const [serviceName, setServiceName] = useState('');
  const [teamName, setTeamName] = useState('');
  const [teamContact, setTeamContact] = useState('');
  const [priority, setPriority] = useState(0);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!projectId || !matchPattern.trim() || !serviceName.trim() || !teamName.trim()) {
      toast.error('Fill in all required fields');
      return;
    }
    try {
      await createOwnershipRule(projectId, {
        match_type: matchType,
        match_pattern: matchPattern.trim(),
        service_name: serviceName.trim(),
        team_name: teamName.trim(),
        team_contact: teamContact.trim() || undefined,
        priority,
      });
      toast.success('Rule created');
      setShowForm(false);
      setMatchPattern('');
      setServiceName('');
      setTeamName('');
      setTeamContact('');
      setPriority(0);
      // The coverage badge above this table is a SEPARATE SWR key
      // (['codeowners-coverage', projectId]) with no poll and no focus
      // revalidation, and this page does not unmount while you edit. Refreshing
      // only the rule list left the badge quoting coverage computed from rules
      // that no longer exist. The CODEOWNERS import path already pairs these
      // two; the three rule mutations did not.
      await Promise.all([refresh(), refreshCoverage()]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create rule');
    }
  };

  const handleToggle = async (rule: OwnershipRule) => {
    if (!projectId) return;
    try {
      await updateOwnershipRule(projectId, rule.id, { is_active: !rule.is_active });
      await Promise.all([refresh(), refreshCoverage()]);
    } catch {
      toast.error('Failed to toggle rule');
    }
  };

  const handleDelete = async (ruleId: string) => {
    if (!projectId || !confirm('Delete this ownership rule?')) return;
    try {
      await deleteOwnershipRule(projectId, ruleId);
      toast.success('Rule deleted');
      await Promise.all([refresh(), refreshCoverage()]);
    } catch {
      toast.error('Failed to delete rule');
    }
  };

  // ── Team notification channels (US-7.3) ────────────────────────────────
  const teamNames = Array.from(
    new Set([...rules.map(r => r.team_name), ...channels.map(c => c.team_name)]),
  ).sort();

  const editFor = (team: string): { channel_type: ChannelType; target: string } => {
    const existing = channels.find(c => c.team_name === team);
    return (
      channelEdits[team] ?? {
        channel_type: existing?.channel_type ?? 'slack',
        target: existing?.target ?? '',
      }
    );
  };

  const handleSaveChannel = async (team: string) => {
    if (!projectId) return;
    const edit = editFor(team);
    if (!edit.target.trim()) {
      toast.error('Enter a webhook URL or email address');
      return;
    }
    try {
      await upsertTeamChannel(projectId, team, {
        channel_type: edit.channel_type,
        target: edit.target.trim(),
      });
      toast.success(`Channel saved for ${team}`);
      setChannelEdits(prev => {
        const next = { ...prev };
        delete next[team];
        return next;
      });
      refreshChannels();
    } catch {
      toast.error('Failed to save team channel');
    }
  };

  const handleRemoveChannel = async (team: string) => {
    if (!projectId || !confirm(`Remove the notification channel for ${team}?`)) return;
    try {
      await deleteTeamChannel(projectId, team);
      toast.success('Team channel removed');
      refreshChannels();
    } catch {
      toast.error('Failed to remove team channel');
    }
  };

  if (!projectId) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold text-[var(--color-text)]">Service Ownership</h1>
        <div className="text-center py-12 text-[var(--color-text-muted)]">
          Select a project to manage ownership rules. Ownership maps tests and failure clusters to responsible teams.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">Service Ownership Map</h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            Define rules that map test suites, components, and packages to the teams that own them.
            Rules are evaluated by priority (highest first).
          </p>
        </div>
        <div className="flex items-center gap-2">
          {coverage && coverage.path_rules > 0 && (
            <span
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)]"
              title={
                coverage.coverage_pct === null
                  ? `Not measurable: none of the ${coverage.sampled} recently-failing test(s) could be traced to a file path (last ${coverage.lookback_days}d), so there is nothing for a path rule to cover. Java stack traces are not located. ${coverage.codeowners_rules} CODEOWNERS rule(s).`
                  : `${coverage.matched}/${coverage.located} recently-failing test paths matched a path rule (last ${coverage.lookback_days}d). ${coverage.codeowners_rules} CODEOWNERS rule(s).`
              }
            >
              Coverage
              {/* An empty denominator is not 0%. Rendering 0% here told a
                  Java-only project its rules covered nothing, which no
                  number of extra rules could ever change. */}
              <strong className="text-[var(--color-text)] tabular-nums">
                {coverage.coverage_pct === null ? 'n/a' : `${coverage.coverage_pct}%`}
              </strong>
            </span>
          )}
          <button onClick={() => setShowImport(true)}
            className="px-4 py-2 border border-[var(--color-border)] text-[var(--color-text-secondary)] rounded-lg hover:bg-[var(--color-bg-hover)] text-sm">
            Import CODEOWNERS
          </button>
          <button onClick={() => setShowForm(!showForm)}
            className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-lg hover:bg-[var(--color-bg-hover)] text-sm">
            {showForm ? 'Cancel' : 'Add Rule'}
          </button>
        </div>
      </div>

      {showImport && (
        <ImportCodeownersDialog
          projectId={projectId}
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false);
            refresh();
            refreshCoverage();
          }}
        />
      )}

      {isError && <div className="bg-[var(--status-failed-bg)]/30 border border-[var(--status-failed-bd)] rounded-lg p-3 text-[var(--status-failed)] text-sm">Failed to load ownership rules</div>}

      {/* New rule form */}
      {showForm && (
        <form onSubmit={handleCreate} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <div>
              <label htmlFor="owner-field-0" className="text-xs text-[var(--color-text-muted)]">Match Type *</label>
              <select id="owner-field-0" value={matchType} onChange={e => setMatchType(e.target.value)}
                className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1">
                {MATCH_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="owner-field-1" className="text-xs text-[var(--color-text-muted)]">Match Pattern * <span className="text-[var(--color-text-muted)]">(glob supported)</span></label>
              <input id="owner-field-1" value={matchPattern} onChange={e => setMatchPattern(e.target.value)}
                placeholder="e.g., auth-* or com.app.payments.*"
                className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" required />
            </div>
            <div>
              <label htmlFor="owner-field-2" className="text-xs text-[var(--color-text-muted)]">Service Name *</label>
              <input id="owner-field-2" value={serviceName} onChange={e => setServiceName(e.target.value)}
                placeholder="e.g., auth-service"
                className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" required />
            </div>
            <div>
              <label htmlFor="owner-field-3" className="text-xs text-[var(--color-text-muted)]">Team Name *</label>
              <input id="owner-field-3" value={teamName} onChange={e => setTeamName(e.target.value)}
                placeholder="e.g., Identity Team"
                className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" required />
            </div>
            <div>
              <label htmlFor="owner-field-4" className="text-xs text-[var(--color-text-muted)]">Team Contact</label>
              <input id="owner-field-4" value={teamContact} onChange={e => setTeamContact(e.target.value)}
                placeholder="e.g., #identity-team or team@example.com"
                className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" />
            </div>
            <div>
              <label htmlFor="owner-field-5" className="text-xs text-[var(--color-text-muted)]">Priority <span className="text-[var(--color-text-muted)]">(higher = first)</span></label>
              <input id="owner-field-5" type="number" min="0" max="1000" value={priority} onChange={e => setPriority(parseInt(e.target.value) || 0)}
                className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" />
            </div>
          </div>
          <button type="submit" className="px-4 py-2 bg-[var(--status-passed-bg)] text-[var(--color-text)] rounded hover:bg-[var(--status-passed-bg)] text-sm">
            Create Rule
          </button>
        </form>
      )}

      {/* Rules list */}
      {loading ? (
        <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div>
      ) : rules.length === 0 ? (
        <div className="text-center py-12 text-[var(--color-text-muted)]">
          No ownership rules configured for this project. Add rules to route failures to the right teams.
        </div>
      ) : (
        <div className="space-y-2">
          <div className="bg-[var(--color-bg-hover)]/50 rounded-lg px-4 py-2 grid grid-cols-7 gap-2 text-xs text-[var(--color-text-muted)] font-medium">
            <span>Type</span><span>Pattern</span><span>Service</span><span>Team</span>
            <span>Contact</span><span>Priority</span><span>Actions</span>
          </div>
          {rules.map(rule => (
            <div key={rule.id} className={clsx(
              'bg-[var(--color-bg-secondary)] rounded-lg px-4 py-3 grid grid-cols-7 gap-2 items-center text-sm',
              !rule.is_active && 'opacity-50',
            )}>
              <span className="text-xs font-mono text-[var(--color-text)]">{rule.match_type}</span>
              <span className="text-[var(--color-text)] truncate font-mono text-xs" title={rule.match_pattern}>{rule.match_pattern}</span>
              <span className="text-[var(--color-text-secondary)] truncate flex items-center gap-1.5">
                {rule.service_name === CODEOWNERS_SERVICE ? (
                  <span
                    className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-[color-mix(in srgb, var(--color-accent) 14%, transparent)] text-[var(--color-accent)] border border-[color-mix(in srgb, var(--color-accent) 30%, transparent)]"
                    title="Imported from a CODEOWNERS file"
                  >
                    CODEOWNERS
                  </span>
                ) : (
                  rule.service_name
                )}
              </span>
              <span className="text-[var(--color-text-secondary)] truncate">{rule.team_name}</span>
              <span className="text-[var(--color-text-muted)] truncate text-xs">{rule.team_contact || '—'}</span>
              <span className="text-[var(--color-text-muted)] text-xs">{rule.priority}</span>
              <div className="flex gap-1.5">
                <button onClick={() => handleToggle(rule)}
                  className={clsx('px-2 py-0.5 rounded text-xs', rule.is_active ? 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]' : 'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]')}>
                  {rule.is_active ? 'Active' : 'Disabled'}
                </button>
                <button onClick={() => handleDelete(rule.id)}
                  className="px-2 py-0.5 rounded text-xs bg-[var(--status-failed-bg)]/30 text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/40">
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Team notification channels (US-7.3) */}
      <div className="space-y-2">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text)]">Team Notification Channels</h2>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            Route transition notifications (newly failing, recovered, newly flaky) for a team's tests
            directly to that team's channel. Teams without a channel fall back to the project defaults.
          </p>
        </div>
        {isChannelsError && (
          <div className="bg-[var(--status-failed-bg)]/30 border border-[var(--status-failed-bd)] rounded-lg p-3 text-[var(--status-failed)] text-sm">
            Failed to load team channels
          </div>
        )}
        {teamNames.length === 0 ? (
          <div className="text-center py-6 text-[var(--color-text-muted)] text-sm">
            No teams yet — teams come from the ownership rules above.
          </div>
        ) : (
          <div className="space-y-2">
            {teamNames.map(team => {
              const existing = channels.find(c => c.team_name === team);
              const edit = editFor(team);
              return (
                <div key={team}
                  className="bg-[var(--color-bg-secondary)] rounded-lg px-4 py-3 grid grid-cols-[1fr_140px_2fr_auto] gap-3 items-center text-sm">
                  <span className="text-[var(--color-text-secondary)] truncate" title={team}>{team}</span>
                  <select value={edit.channel_type}
                    onChange={e => setChannelEdits(prev => ({
                      ...prev,
                      [team]: { ...edit, channel_type: e.target.value as ChannelType },
                    }))}
                    className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-2 py-1.5 text-xs">
                    {CHANNEL_TYPES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                  <input value={edit.target}
                    onChange={e => setChannelEdits(prev => ({
                      ...prev,
                      [team]: { ...edit, target: e.target.value },
                    }))}
                    placeholder={edit.channel_type === 'email' ? 'team@example.com' : 'https://hooks…'}
                    className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-1.5 text-xs font-mono" />
                  <div className="flex gap-1.5">
                    <button onClick={() => handleSaveChannel(team)}
                      className="px-2 py-0.5 rounded text-xs bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)] hover:bg-[var(--status-passed-bg)]/40">
                      Save
                    </button>
                    {existing && (
                      <button onClick={() => handleRemoveChannel(team)}
                        className="px-2 py-0.5 rounded text-xs bg-[var(--status-failed-bg)]/30 text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/40">
                        Remove
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Help section */}
      <div className="bg-[var(--color-bg-hover)]/50 rounded-lg p-4 text-xs text-[var(--color-text-muted)]">
        <h3 className="text-[var(--color-text-muted)] font-medium mb-2">How ownership resolution works</h3>
        <ol className="list-decimal list-inside space-y-1">
          <li>When a failure cluster is analyzed, each member test is matched against rules (highest priority first)</li>
          <li>The team that owns the most tests in the cluster wins (majority vote)</li>
          <li>If no rule matches, the system falls back to the project's component_owner_map, then to Allure @Owner labels</li>
          <li>Resolved ownership appears on cluster cards and pre-fills defect promotion forms</li>
        </ol>
        <p className="mt-2">Patterns support glob syntax: <code className="bg-[var(--color-bg)] px-1 rounded">auth-*</code> matches <code className="bg-[var(--color-bg)] px-1 rounded">auth-login</code>, <code className="bg-[var(--color-bg)] px-1 rounded">auth-register</code>, etc.</p>
      </div>
    </div>
  );
}

// ── Import CODEOWNERS dialog (US-8.3) ─────────────────────────────────────────

function ImportCodeownersDialog({
  projectId,
  onClose,
  onImported,
}: {
  projectId: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const [source, setSource] = useState<'github' | 'text'>('text');
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Preview count: non-comment, non-blank lines that carry an owner token.
  // Cheap local heuristic so the confirm button can show what will be imported.
  const previewCount =
    source === 'text'
      ? text
          .split('\n')
          .map(l => l.trim())
          .filter(l => l && !l.startsWith('#') && l.split(/\s+/).length >= 2).length
      : null;

  const handleImport = async () => {
    if (source === 'text' && !text.trim()) {
      toast.error('Paste a CODEOWNERS file or switch to GitHub fetch');
      return;
    }
    setSubmitting(true);
    try {
      const result = await importCodeowners(projectId, {
        source,
        text: source === 'text' ? text : undefined,
      });
      toast.success(
        `Imported ${result.rules_created} rule${result.rules_created === 1 ? '' : 's'}` +
          (result.rules_replaced ? ` (replaced ${result.rules_replaced})` : ''),
      );
      onImported();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || (err instanceof Error ? err.message : 'Import failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl w-full max-w-lg p-5"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-sm font-semibold text-[var(--color-text)] m-0 mb-1">Import CODEOWNERS</h2>
        <p className="text-xs text-[var(--color-text-muted)] mb-3">
          Maps each CODEOWNERS line to a <code className="font-mono">path</code> ownership rule.
          Re-importing replaces prior CODEOWNERS-sourced rules and leaves hand-authored rules untouched.
        </p>

        <div role="radiogroup" aria-label="Import source" className="flex items-center gap-1.5 mb-3">
          {(['text', 'github'] as const).map(opt => {
            const active = source === opt;
            return (
              <button
                key={opt}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setSource(opt)}
                className={clsx(
                  'px-3 py-1.5 text-xs rounded-full border transition-colors',
                  active
                    ? 'bg-[color-mix(in srgb, var(--color-accent) 14%, transparent)] border-[color-mix(in srgb, var(--color-accent) 30%, transparent)] text-[var(--color-accent)]'
                    : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)]',
                )}
              >
                {opt === 'text' ? 'Paste text' : 'Fetch from GitHub'}
              </button>
            );
          })}
        </div>

        {source === 'text' ? (
          <>
            <textarea
              value={text}
              onChange={e => setText(e.target.value)}
              rows={10}
              placeholder={'# comment\nsrc/api/**  @org/backend\ndocs/  @alice\n'}
              className="w-full text-xs font-mono px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
            />
            {previewCount != null && (
              <p className="text-[11px] text-[var(--color-text-muted)] mt-1">
                {previewCount} owner line{previewCount === 1 ? '' : 's'} detected
              </p>
            )}
          </>
        ) : (
          <div className="text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded px-3 py-3">
            Fetches <code className="font-mono">.github/CODEOWNERS</code> (then{' '}
            <code className="font-mono">CODEOWNERS</code>, <code className="font-mono">docs/CODEOWNERS</code>)
            over the project&apos;s GitHub integration. Requires the integration configured and enabled,
            and offline mode off.
          </div>
        )}

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="text-xs px-3 py-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleImport}
            disabled={submitting || (source === 'text' && !text.trim())}
            className="text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] px-3 py-1.5 rounded-lg font-medium"
          >
            {submitting ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}
