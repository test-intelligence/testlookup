import { ChevronLeft, ChevronRight } from 'lucide-react'

interface Props { page: number; pages: number; total: number; onChange: (p: number) => void }

export default function Pagination({ page, pages, total, onChange }: Props) {
  if (pages <= 1) return null
  return (
    <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--color-border)]">
      <p className="text-xs text-[var(--color-text-muted)]">{total} total results</p>
      <div className="flex items-center gap-1">
        <button
          className="btn-ghost disabled:opacity-30"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="text-xs text-[var(--color-text-muted)] px-2">Page {page} of {pages}</span>
        <button
          className="btn-ghost disabled:opacity-30"
          disabled={page >= pages}
          onClick={() => onChange(page + 1)}
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}
