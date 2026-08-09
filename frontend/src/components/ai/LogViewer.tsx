import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import toast from 'react-hot-toast'
import { copyTextToClipboard } from '@/utils/clipboard'

interface Props { content?: string; title?: string }

export default function LogViewer({ content, title = 'Stack Trace' }: Props) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    if (!content) return
    const ok = await copyTextToClipboard(content)
    if (!ok) {
      toast.error('Clipboard access denied — copy manually')
      return
    }
    setCopied(true)
    toast.success('Copied to clipboard')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="rounded-xl overflow-hidden border border-[var(--color-border)]">
      {/* Terminal title bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border)]">
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5">
            <div className="h-3 w-3 rounded-full bg-[var(--status-failed-bg)]/70" />
            <div className="h-3 w-3 rounded-full bg-[var(--status-broken-bg)]/70" />
            <div className="h-3 w-3 rounded-full bg-[var(--status-passed-bg)]/70" />
          </div>
          <span className="text-xs text-[var(--color-text-muted)] font-mono ml-2">{title}</span>
        </div>
        <button
          onClick={handleCopy}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors p-1 rounded"
          disabled={!content}
        >
          {copied
            ? <Check className="h-3.5 w-3.5 text-[var(--status-passed)]" />
            : <Copy className="h-3.5 w-3.5" />
          }
        </button>
      </div>

      {/* Log content */}
      <div className="bg-[var(--color-bg)] overflow-auto max-h-96">
        {content ? (
          <pre className="p-4 text-xs font-mono text-[var(--color-text-secondary)] leading-relaxed whitespace-pre-wrap break-words">
            {content}
          </pre>
        ) : (
          <div className="flex items-center justify-center h-32 text-[var(--color-text-faint)] text-sm font-mono">
            No log content available
          </div>
        )}
      </div>
    </div>
  )
}
