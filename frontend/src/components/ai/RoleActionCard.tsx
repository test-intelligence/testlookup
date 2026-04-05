import { useState } from 'react'
import { clsx } from 'clsx'
import { Copy, Check, User, Code, Server, Shield } from 'lucide-react'

const ROLE_CONFIG: Record<string, { label: string; icon: React.ElementType; colour: string }> = {
  qa:              { label: 'QA',              icon: User,   colour: 'text-[var(--color-text)]' },
  developer:       { label: 'Developer',       icon: Code,   colour: 'text-purple-400' },
  sre:             { label: 'SRE',             icon: Server, colour: 'text-orange-400' },
  release_manager: { label: 'Release Manager', icon: Shield, colour: 'text-emerald-400' },
}

interface RoleActionCardProps {
  roleActions: Record<string, string>
  filterRoles?: string[] | null   // if set, only show these roles
  compact?: boolean               // condensed single-column layout
}

export default function RoleActionCard({ roleActions, filterRoles, compact }: RoleActionCardProps) {
  const [copiedRole, setCopiedRole] = useState<string | null>(null)
  const [activeFilter, setActiveFilter] = useState<Set<string>>(
    new Set(filterRoles ?? Object.keys(ROLE_CONFIG))
  )

  const roles = Object.entries(ROLE_CONFIG).filter(
    ([key]) => roleActions[key] && activeFilter.has(key)
  )

  if (roles.length === 0) return null

  const handleCopy = (role: string, text: string) => {
    navigator.clipboard.writeText(text)
    setCopiedRole(role)
    setTimeout(() => setCopiedRole(null), 1500)
  }

  const showFilterChips = !filterRoles  // only show filter chips if no preset filter

  return (
    <div className="space-y-2">
      {/* Filter chips */}
      {showFilterChips && Object.keys(roleActions).length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(ROLE_CONFIG)
            .filter(([key]) => roleActions[key])
            .map(([key, cfg]) => (
              <button
                key={key}
                onClick={() => {
                  const next = new Set(activeFilter)
                  if (next.has(key)) next.delete(key); else next.add(key)
                  if (next.size > 0) setActiveFilter(next)
                }}
                className={clsx(
                  'px-2 py-0.5 rounded text-[10px] font-medium transition-colors border',
                  activeFilter.has(key)
                    ? `${cfg.colour} border-current bg-[var(--color-bg-secondary)]`
                    : 'text-[var(--color-text-faint)] border-[var(--color-border)] bg-transparent hover:text-[var(--color-text-muted)]',
                )}
              >
                {cfg.label}
              </button>
            ))}
        </div>
      )}

      {/* Action cards */}
      <div className={clsx(compact ? 'space-y-1.5' : 'grid grid-cols-1 md:grid-cols-2 gap-2')}>
        {roles.map(([key, cfg]) => {
          const Icon = cfg.icon
          return (
            <div
              key={key}
              className="bg-[var(--color-bg-secondary)]/60 rounded-lg px-3 py-2 flex items-start gap-2 group"
            >
              <Icon className={clsx('h-4 w-4 mt-0.5 shrink-0', cfg.colour)} />
              <div className="flex-1 min-w-0">
                <p className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wider">{cfg.label}</p>
                <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed">{roleActions[key]}</p>
              </div>
              <button
                onClick={() => handleCopy(key, roleActions[key])}
                className="opacity-0 group-hover:opacity-100 transition-opacity text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] mt-0.5"
                title="Copy to clipboard"
              >
                {copiedRole === key
                  ? <Check className="h-3.5 w-3.5 text-emerald-400" />
                  : <Copy className="h-3.5 w-3.5" />}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
