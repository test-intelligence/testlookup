import { ReactNode } from 'react'

interface Props {
  title: string
  subtitle?: string
  actions?: ReactNode
  className?: string
}

export default function PageHeader({ title, subtitle, actions, className }: Props) {
  return (
    <div className={`flex flex-col gap-3 md:flex-row md:items-start md:justify-between mb-5 ${className ?? ''}`.trim()}>
      <div className="max-w-3xl">
        <h1 className="text-2xl font-bold text-[var(--color-text)]">{title}</h1>
        {subtitle && <p className="text-sm text-[var(--color-text-muted)] mt-1">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
