import { Link } from 'react-router-dom'
import { Activity, Bot, BrainCircuit, Database, Gauge, Key, Bell, ChevronRight, FileSearch, Fingerprint, Mail, ShieldAlert } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import { usePermissions } from '@/hooks/usePermissions'

const sections = [
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
]

export default function SettingsPage() {
  const { canViewSettings } = usePermissions()

  if (!canViewSettings) {
    return (
      <div className="space-y-4">
        <PageHeader title="Settings" subtitle="Application configuration and integrations" />
        <div className="flex flex-col items-center py-20 text-[var(--color-text-muted)]">
          <ShieldAlert className="h-10 w-10 mb-3 text-[var(--color-text-faint)]" />
          <p className="font-medium">Access Restricted</p>
          <p className="text-sm mt-1">You need QA Lead or Admin role to view settings.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" subtitle="Application configuration and integrations" />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {sections.map(({ icon: Icon, title, desc, href }) => (
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
