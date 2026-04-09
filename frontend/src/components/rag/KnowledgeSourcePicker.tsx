import { useState } from 'react'
import { Check, Database, ExternalLink, FileText, Loader2, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import type { KnowledgeSource } from '@/types/rag-generation'

const SOURCE_ICONS: Record<string, typeof Database> = {
  jira_issue: ExternalLink,
  jira_epic: ExternalLink,
  confluence_page: FileText,
  uploaded_document: FileText,
  internal_url: ExternalLink,
  external_url: ExternalLink,
}

const SYNC_COLORS: Record<string, string> = {
  synced: 'text-emerald-400',
  pending: 'text-amber-400',
  syncing: 'text-blue-400',
  failed: 'text-red-400',
}

interface Props {
  sources: KnowledgeSource[]
  selectedIds: string[]
  onToggle: (id: string) => void
  onSync: (id: string) => void
  loading?: boolean
}

export default function KnowledgeSourcePicker({ sources, selectedIds, onToggle, onSync, loading }: Props) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] py-4">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading knowledge sources...
      </div>
    )
  }

  if (!sources.length) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] py-4">
        No knowledge sources registered for this project. Add sources in the Knowledge Sources settings.
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
        Select Knowledge Sources
      </p>
      {sources.filter(s => !s.is_archived).map(source => {
        const Icon = SOURCE_ICONS[source.source_type] || Database
        const isSelected = selectedIds.includes(source.id)
        const syncColor = SYNC_COLORS[source.sync_status] || 'text-[var(--color-text-faint)]'

        return (
          <div
            key={source.id}
            className={clsx(
              'flex items-center gap-3 px-3 py-2 rounded-lg border cursor-pointer transition-colors',
              isSelected
                ? 'border-blue-500/50 bg-blue-500/10'
                : 'border-[var(--color-border)] hover:border-[var(--color-border-light)]',
            )}
            onClick={() => onToggle(source.id)}
          >
            <div className={clsx(
              'h-5 w-5 rounded border flex items-center justify-center flex-shrink-0',
              isSelected ? 'bg-blue-500 border-blue-500' : 'border-[var(--color-border)]',
            )}>
              {isSelected && <Check className="h-3 w-3 text-white" />}
            </div>
            <Icon className="h-4 w-4 text-[var(--color-text-muted)] flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm text-[var(--color-text)] truncate">{source.title}</p>
              <p className="text-xs text-[var(--color-text-faint)] truncate">{source.source_type.replace('_', ' ')}</p>
            </div>
            <span className={clsx('text-xs', syncColor)}>{source.sync_status}</span>
            {source.sync_status !== 'syncing' && (
              <button
                onClick={e => { e.stopPropagation(); onSync(source.id) }}
                className="p-1 rounded hover:bg-[var(--color-bg-secondary)] transition-colors"
                title="Sync source"
              >
                <RefreshCw className="h-3 w-3 text-[var(--color-text-muted)]" />
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
