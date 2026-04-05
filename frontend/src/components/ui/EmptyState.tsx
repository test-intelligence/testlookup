import { ReactNode } from 'react'

interface Props { icon: ReactNode; title: string; description?: string; action?: ReactNode }

export default function EmptyState({ icon, title, description, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="p-4 bg-[var(--color-bg-secondary)] rounded-2xl mb-4 text-[var(--color-text-muted)]">{icon}</div>
      <h3 className="text-lg font-semibold text-[var(--color-text-secondary)] mb-1">{title}</h3>
      {description && <p className="text-sm text-[var(--color-text-muted)] max-w-sm mb-4">{description}</p>}
      {action}
    </div>
  )
}
