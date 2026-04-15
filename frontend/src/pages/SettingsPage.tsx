import { Link } from 'react-router-dom'
import { Activity, Bot, BrainCircuit, Database, DollarSign, Flag, Gauge, Github, Key, Bell, ChevronRight, FileSearch, Fingerprint, Mail, Sprout, ShieldAlert, UserCircle2, Webhook } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import { usePermissions } from '@/hooks/usePermissions'

const isDev = import.meta.env.DEV

const sections = [
  {
    icon: UserCircle2,
    title: 'My Profile',
    desc: 'Update your display name, avatar colour, and password',
    href: '/settings/profile',
    allRoles: true,
  },
  {
    icon: Bot,
    title: 'AI Configuration',
    desc: 'LLM provider, model selection, offline mode toggle',
    href: '/settings/ai',
  },
  {
    icon: Database,
    title: 'Data & Storage',
    desc: 'PostgreSQL, MongoDB, MinIO, ChromaDB connection settings',
    href: '/settings/storage',
  },
  {
    icon: Key,
    title: 'Integrations',
    desc: 'Jira, Splunk, OpenShift API, Slack, Microsoft Teams',
    href: '/settings/integrations',
  },
  {
    icon: Bell,
    title: 'Notifications',
    desc: 'Email, Slack, and Teams alert rules per project',
    href: '/settings/notifications',
  },
  {
    icon: Fingerprint,
    title: 'SSO & Identity',
    desc: 'SAML SSO configuration, SCIM provisioning, identity audit events',
    href: '/settings/sso',
  },
  {
    icon: Mail,
    title: 'Digests & Views',
    desc: 'Scheduled quality digests, saved filter views, subscription management',
    href: '/settings/digests',
  },
  {
    icon: Activity,
    title: 'Integration Health',
    desc: 'Active health probes for Jira, Splunk, GitHub, Slack, SMTP, and more',
    href: '/settings/integration-health',
  },
  {
    icon: FileSearch,
    title: 'Audit Dashboard',
    desc: 'Unified audit trail for security, releases, config changes, and tenant metrics',
    href: '/settings/audit',
  },
  {
    icon: BrainCircuit,
    title: 'AI Evaluation',
    desc: 'Measure AI quality: precision, recall, agreement, drift, model version history',
    href: '/settings/ai-eval',
  },
  {
    icon: Gauge,
    title: 'Performance',
    desc: 'Latency budgets, search indexing config, and scale scenarios',
    href: '/settings/performance',
  },
  {
    icon: Flag,
    title: 'Feature Flags',
    desc: 'Gate capabilities by global kill switch, project, role, or rollout percent',
    href: '/settings/feature-flags',
  },
  {
    icon: DollarSign,
    title: 'LLM Cost Budget',
    desc: 'Usage-based billing: per-project spend caps, at-cap downgrade policy, workspace overview',
    href: '/settings/billing',
  },
  {
    icon: Github,
    title: 'GitHub Integration',
    desc: 'Post check runs to PRs on every test run — per-project repo + token + offline-mode aware',
    href: '/settings/github',
  },
  {
    icon: Webhook,
    title: 'Outbound Webhooks',
    desc: 'Subscribe external systems to run.completed, defect.promoted, release.decided, flaky.quarantined, quota.exceeded',
    href: '/settings/webhooks',
  },
  ...(isDev
    ? [
        {
          icon: Sprout,
          title: 'Seed Data',
          desc: 'Load, reset, or delete demo data for the dev environment',
          href: '/settings/seed-data',
        },
      ]
    : []),
]

export default function SettingsPage() {
  const { canViewSettings } = usePermissions()

  // Profile card is always visible; other cards require QA_LEAD+
  const visibleSections = sections.filter(s => s.allRoles || canViewSettings)

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" subtitle="Application configuration and integrations" />

      {!canViewSettings && (
        <div className="card flex items-center gap-3 border-amber-700/30 bg-amber-900/10 py-3 px-4">
          <ShieldAlert className="h-4 w-4 text-amber-400 flex-shrink-0" />
          <p className="text-sm text-amber-300">Some settings require QA Lead or Admin role.</p>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {visibleSections.map(({ icon: Icon, title, desc, href }) => (
          <Link key={title} to={href} className="block">
            <div className="card hover:border-[var(--color-border-light)] transition-colors cursor-pointer">
              <div className="flex items-center gap-3 mb-2">
                <div className="p-2 bg-[var(--color-bg-secondary)] rounded-lg"><Icon className="h-4 w-4 text-[var(--color-text-muted)]" /></div>
                <h3 className="font-semibold text-[var(--color-text)] flex-1">{title}</h3>
                <ChevronRight className="h-4 w-4 text-[var(--color-text-muted)]" />
              </div>
              <p className="text-sm text-[var(--color-text-muted)] pl-11">{desc}</p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
