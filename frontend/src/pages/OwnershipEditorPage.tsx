import { useState } from 'react';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import {
  type OwnershipRule,
  MATCH_TYPES,
  createOwnershipRule,
  deleteOwnershipRule,
  deleteTeamChannel,
  updateOwnershipRule,
  upsertTeamChannel,
} from '../services/ownershipService';
import { useProjectStore, ALL_PROJECTS_ID } from '../store/projectStore';
import { useOwnershipRules } from '../hooks/useOwnershipRules';
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
  const {
    channels,
    isError: isChannelsError,
    refresh: refreshChannels,
  } = useTeamChannels(projectId);

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
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create rule');
    }
  };

  const handleToggle = async (rule: OwnershipRule) => {
    if (!projectId) return;
    try {
      await updateOwnershipRule(projectId, rule.id, { is_active: !rule.is_active });
      refresh();
    } catch {
      toast.error('Failed to toggle rule');
    }
  };

  const handleDelete = async (ruleId: string) => {
    if (!projectId || !confirm('Delete this ownership rule?')) return;
    try {
      await deleteOwnershipRule(projectId, ruleId);
      toast.success('Rule deleted');
      refresh();
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
        <h1 className="text-2xl font-bold text-gray-100">Service Ownership</h1>
        <div className="text-center py-12 text-gray-500">
          Select a project to manage ownership rules. Ownership maps tests and failure clusters to responsible teams.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Service Ownership Map</h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            Define rules that map test suites, components, and packages to the teams that own them.
            Rules are evaluated by priority (highest first).
          </p>
        </div>
        <button onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-lg hover:bg-neutral-200 text-sm">
          {showForm ? 'Cancel' : 'Add Rule'}
        </button>
      </div>

      {isError && <div className="bg-red-900/30 border border-red-700 rounded-lg p-3 text-red-300 text-sm">Failed to load ownership rules</div>}

      {/* New rule form */}
      {showForm && (
        <form onSubmit={handleCreate} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-[var(--color-text-muted)]">Match Type *</label>
              <select value={matchType} onChange={e => setMatchType(e.target.value)}
                className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm mt-1">
                {MATCH_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-muted)]">Match Pattern * <span className="text-gray-600">(glob supported)</span></label>
              <input value={matchPattern} onChange={e => setMatchPattern(e.target.value)}
                placeholder="e.g., auth-* or com.app.payments.*"
                className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm mt-1" required />
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-muted)]">Service Name *</label>
              <input value={serviceName} onChange={e => setServiceName(e.target.value)}
                placeholder="e.g., auth-service"
                className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm mt-1" required />
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-muted)]">Team Name *</label>
              <input value={teamName} onChange={e => setTeamName(e.target.value)}
                placeholder="e.g., Identity Team"
                className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm mt-1" required />
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-muted)]">Team Contact</label>
              <input value={teamContact} onChange={e => setTeamContact(e.target.value)}
                placeholder="e.g., #identity-team or team@example.com"
                className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm mt-1" />
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-muted)]">Priority <span className="text-gray-600">(higher = first)</span></label>
              <input type="number" min="0" max="1000" value={priority} onChange={e => setPriority(parseInt(e.target.value) || 0)}
                className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm mt-1" />
            </div>
          </div>
          <button type="submit" className="px-4 py-2 bg-green-600 text-[var(--color-text)] rounded hover:bg-green-700 text-sm">
            Create Rule
          </button>
        </form>
      )}

      {/* Rules list */}
      {loading ? (
        <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div>
      ) : rules.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          No ownership rules configured for this project. Add rules to route failures to the right teams.
        </div>
      ) : (
        <div className="space-y-2">
          <div className="bg-[var(--color-bg-hover)]/50 rounded-lg px-4 py-2 grid grid-cols-7 gap-2 text-xs text-gray-500 font-medium">
            <span>Type</span><span>Pattern</span><span>Service</span><span>Team</span>
            <span>Contact</span><span>Priority</span><span>Actions</span>
          </div>
          {rules.map(rule => (
            <div key={rule.id} className={clsx(
              'bg-[var(--color-bg-secondary)] rounded-lg px-4 py-3 grid grid-cols-7 gap-2 items-center text-sm',
              !rule.is_active && 'opacity-50',
            )}>
              <span className="text-xs font-mono text-[var(--color-text)]">{rule.match_type}</span>
              <span className="text-neutral-200 truncate font-mono text-xs" title={rule.match_pattern}>{rule.match_pattern}</span>
              <span className="text-gray-300 truncate">{rule.service_name}</span>
              <span className="text-gray-300 truncate">{rule.team_name}</span>
              <span className="text-gray-500 truncate text-xs">{rule.team_contact || '—'}</span>
              <span className="text-[var(--color-text-muted)] text-xs">{rule.priority}</span>
              <div className="flex gap-1.5">
                <button onClick={() => handleToggle(rule)}
                  className={clsx('px-2 py-0.5 rounded text-xs', rule.is_active ? 'bg-green-900/40 text-green-400' : 'bg-gray-700 text-[var(--color-text-muted)]')}>
                  {rule.is_active ? 'Active' : 'Disabled'}
                </button>
                <button onClick={() => handleDelete(rule.id)}
                  className="px-2 py-0.5 rounded text-xs bg-red-900/30 text-red-400 hover:bg-red-800/40">
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
          <h2 className="text-lg font-semibold text-gray-100">Team Notification Channels</h2>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            Route transition notifications (newly failing, recovered, newly flaky) for a team's tests
            directly to that team's channel. Teams without a channel fall back to the project defaults.
          </p>
        </div>
        {isChannelsError && (
          <div className="bg-red-900/30 border border-red-700 rounded-lg p-3 text-red-300 text-sm">
            Failed to load team channels
          </div>
        )}
        {teamNames.length === 0 ? (
          <div className="text-center py-6 text-gray-500 text-sm">
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
                  <span className="text-gray-300 truncate" title={team}>{team}</span>
                  <select value={edit.channel_type}
                    onChange={e => setChannelEdits(prev => ({
                      ...prev,
                      [team]: { ...edit, channel_type: e.target.value as ChannelType },
                    }))}
                    className="bg-gray-700 text-gray-100 rounded px-2 py-1.5 text-xs">
                    {CHANNEL_TYPES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                  <input value={edit.target}
                    onChange={e => setChannelEdits(prev => ({
                      ...prev,
                      [team]: { ...edit, target: e.target.value },
                    }))}
                    placeholder={edit.channel_type === 'email' ? 'team@example.com' : 'https://hooks…'}
                    className="bg-gray-700 text-gray-100 rounded px-3 py-1.5 text-xs font-mono" />
                  <div className="flex gap-1.5">
                    <button onClick={() => handleSaveChannel(team)}
                      className="px-2 py-0.5 rounded text-xs bg-green-900/40 text-green-400 hover:bg-green-800/40">
                      Save
                    </button>
                    {existing && (
                      <button onClick={() => handleRemoveChannel(team)}
                        className="px-2 py-0.5 rounded text-xs bg-red-900/30 text-red-400 hover:bg-red-800/40">
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
      <div className="bg-[var(--color-bg-hover)]/50 rounded-lg p-4 text-xs text-gray-500">
        <h3 className="text-[var(--color-text-muted)] font-medium mb-2">How ownership resolution works</h3>
        <ol className="list-decimal list-inside space-y-1">
          <li>When a failure cluster is analyzed, each member test is matched against rules (highest priority first)</li>
          <li>The team that owns the most tests in the cluster wins (majority vote)</li>
          <li>If no rule matches, the system falls back to the project's component_owner_map, then to Allure @Owner labels</li>
          <li>Resolved ownership appears on cluster cards and pre-fills defect promotion forms</li>
        </ol>
        <p className="mt-2">Patterns support glob syntax: <code className="bg-gray-900 px-1 rounded">auth-*</code> matches <code className="bg-gray-900 px-1 rounded">auth-login</code>, <code className="bg-gray-900 px-1 rounded">auth-register</code>, etc.</p>
      </div>
    </div>
  );
}
