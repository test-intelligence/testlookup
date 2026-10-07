/**
 * Team channels (UX redesign P5 item 2; the feature is PMF US-7.3).
 *
 * A team's transition alerts (newly failing, recovered, newly flaky, cluster
 * groups) go straight to that team's own Slack, Teams or email channel instead
 * of only through each person's notification preferences
 * (`notification_routing.partition_transitions`). Until P5 this was the last
 * section of Ownership; it is a notification setting, and now has its own
 * page beside Ownership in the settings sub-nav. Ownership links here.
 *
 * Teams are the `team_name`s of the project's ownership rules, plus any team
 * that already has a channel (a rule can be deleted after its team got one).
 * Everything is per project: the page needs one project, as Ownership does.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import ScopedLink from '@/components/ui/ScopedLink'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import { helpTopicParam } from '@/components/help/helpTopics'
import { type TeamChannel, deleteTeamChannel, upsertTeamChannel } from '@/services/ownershipService'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { useOwnershipRules } from '@/hooks/useOwnershipRules'
import { useTeamChannels } from '@/hooks/useTeamChannels'

type ChannelType = TeamChannel['channel_type']

interface ChannelEdit {
  channel_type: ChannelType
  target: string
}

const CHANNEL_TYPES: Array<{ value: ChannelType; label: string }> = [
  { value: 'slack', label: 'Slack webhook' },
  { value: 'teams', label: 'Teams webhook' },
  { value: 'email', label: 'Email' },
]

const HELP_TOPIC = helpTopicParam('/settings/team-channels')

const ALERT = 'bg-[var(--status-failed-bg)]/30 border border-[var(--status-failed-bd)] rounded-lg p-3 text-[var(--status-failed)] text-sm'
const TH = 'px-4 py-2 text-left text-xs font-medium text-[var(--color-text-muted)]'

/** What the dispatcher does with this team's transitions today (`load_team_channels` reads active rows only). */
function routingOf(existing: TeamChannel | undefined): { label: string; className: string } {
  if (!existing) return { label: 'Not routed', className: 'text-[var(--color-text-muted)]' }
  if (!existing.is_active) return { label: 'Paused', className: 'text-[var(--status-broken)]' }
  return { label: 'Routed to its channel', className: 'text-[var(--status-passed)]' }
}

export default function TeamChannelsPage() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId

  const { rules, isLoading: rulesLoading, isError: isRulesError } = useOwnershipRules(projectId)
  const {
    channels,
    isLoading: channelsLoading,
    isError: isChannelsError,
    refresh: refreshChannels,
  } = useTeamChannels(projectId)

  // Edit buffers, keyed by team name; a team with none shows what is stored.
  const [channelEdits, setChannelEdits] = useState<Record<string, ChannelEdit>>({})

  const teamNames = Array.from(
    new Set([...rules.map(r => r.team_name), ...channels.map(c => c.team_name)]),
  ).sort()

  const editFor = (team: string): ChannelEdit => {
    const existing = channels.find(c => c.team_name === team)
    return (
      channelEdits[team] ?? {
        channel_type: existing?.channel_type ?? 'slack',
        target: existing?.target ?? '',
      }
    )
  }

  const handleSaveChannel = async (team: string) => {
    if (!projectId) return
    const edit = editFor(team)
    if (!edit.target.trim()) {
      toast.error('Enter a webhook URL or email address')
      return
    }
    try {
      await upsertTeamChannel(projectId, team, {
        channel_type: edit.channel_type,
        target: edit.target.trim(),
      })
      toast.success(`Channel saved for ${team}`)
      setChannelEdits(prev => {
        const next = { ...prev }
        delete next[team]
        return next
      })
      refreshChannels()
    } catch {
      toast.error('Failed to save team channel')
    }
  }

  const handleRemoveChannel = async (team: string) => {
    if (!projectId || !confirm(`Remove the notification channel for ${team}?`)) return
    try {
      await deleteTeamChannel(projectId, team)
      toast.success('Team channel removed')
      refreshChannels()
    } catch {
      toast.error('Failed to remove team channel')
    }
  }

  const header = (
    <PageHeader
      compact
      title="Team channels"
      subtitle="Send a team's transition alerts (newly failing, recovered, newly flaky) to the team's own channel"
      helpTopic={HELP_TOPIC}
    />
  )

  if (!projectId) {
    return (
      <div className="space-y-4">
        {header}
        <ProjectRequiredEmptyState description="A team's channel is set per project: pick the project whose teams you are routing." />
      </div>
    )
  }

  const loading = rulesLoading || channelsLoading

  return (
    <div className="space-y-4">
      {header}

      {isRulesError && (
        <div role="alert" className={ALERT}>
          Failed to load the ownership rules, so teams that have no channel yet are missing below
        </div>
      )}
      {isChannelsError && (
        <div role="alert" className={ALERT}>
          Failed to load team channels
        </div>
      )}

      {/* The one table of the page: each team, its channel, and Save / Remove on the row. */}
      <div data-primary="" className="card !p-0 overflow-hidden">
        <table aria-label="Team channels" className="w-full table-fixed text-sm">
          {/* The channel column fits "Slack webhook" and its arrow on CI's wider DejaVu too (w-36 clipped it). */}
          <colgroup>
            <col className="w-[24%]" />
            <col className="w-48" />
            <col />
            <col className="w-36" />
          </colgroup>
          <thead className="bg-[var(--color-bg-secondary)]">
            <tr>
              <th scope="col" className={TH}>Team</th>
              <th scope="col" className={TH}>Channel</th>
              <th scope="col" className={TH}>Webhook URL or email</th>
              <th scope="col" className={TH}>Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {loading ? (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-[var(--color-text-muted)]">Loading...</td>
              </tr>
            ) : teamNames.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-[var(--color-text-muted)]">
                  No teams yet. Teams come from the ownership rules:{' '}
                  <ScopedLink to="/ownership" hintInTitleOnly className="text-[var(--color-accent)] hover:underline">add a rule on Ownership</ScopedLink>.
                </td>
              </tr>
            ) : (
              teamNames.map(team => {
                const existing = channels.find(c => c.team_name === team)
                const edit = editFor(team)
                const routing = routingOf(existing)
                return (
                  <tr key={team} data-team-row={team}>
                    <td className="px-4 py-2.5 align-middle">
                      <div className="truncate text-[var(--color-text)]" title={team}>{team}</div>
                      <div className={`text-[11px] ${routing.className}`}>{routing.label}</div>
                    </td>
                    <td className="px-4 py-2.5 align-middle">
                      <select
                        aria-label={`Channel type for ${team}`}
                        value={edit.channel_type}
                        onChange={e => setChannelEdits(prev => ({
                          ...prev,
                          [team]: { ...edit, channel_type: e.target.value as ChannelType },
                        }))}
                        className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded px-2 py-1.5 text-xs"
                      >
                        {CHANNEL_TYPES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                      </select>
                    </td>
                    <td className="px-4 py-2.5 align-middle">
                      <input
                        aria-label={`Webhook URL or email for ${team}`}
                        value={edit.target}
                        onChange={e => setChannelEdits(prev => ({
                          ...prev,
                          [team]: { ...edit, target: e.target.value },
                        }))}
                        placeholder={edit.channel_type === 'email' ? 'team@example.com' : 'https://hooks…'}
                        className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded px-3 py-1.5 text-xs font-mono"
                      />
                    </td>
                    <td className="px-4 py-2.5 align-middle">
                      <div className="flex gap-1.5">
                        <button
                          onClick={() => handleSaveChannel(team)}
                          className="px-2 py-0.5 rounded text-xs bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)] hover:bg-[var(--status-passed-bg)]/40"
                        >
                          Save
                        </button>
                        {existing && (
                          <button
                            onClick={() => handleRemoveChannel(team)}
                            className="px-2 py-0.5 rounded text-xs bg-[var(--status-failed-bg)]/30 text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/40"
                          >
                            Remove
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
        A test&apos;s team is the one its{' '}
        <ScopedLink to="/ownership" hintInTitleOnly className="text-[var(--color-accent)] hover:underline">ownership rules</ScopedLink>{' '}
        name; a cluster goes to the team that owns more than half of its tests. A team without a channel, a paused one, an
        unowned test and a cluster of mixed ownership go out through each person&apos;s own{' '}
        <Link to="/settings/my-notifications" className="text-[var(--color-accent)] hover:underline">notification settings</Link>{' '}
        instead, and so does a delivery to a team&apos;s channel that fails.
      </p>
    </div>
  )
}
