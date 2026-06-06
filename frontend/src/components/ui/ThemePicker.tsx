import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { THEMES, useThemeStore } from '@/store/themeStore'

/**
 * Color-theme picker. A dropdown listing every theme in the registry with a
 * swatch + label + hint + checkmark. Drop-in replacement for the old binary
 * ThemeToggle; mounted in the TopBar. Persistence + first-paint apply live in
 * the theme store.
 */
export default function ThemePicker() {
  const theme = useThemeStore((s) => s.theme)
  const setTheme = useThemeStore((s) => s.setTheme)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const active = THEMES.find((t) => t.id === theme) ?? THEMES[0]

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 h-9 px-3 rounded-full text-xs font-semibold border transition-colors"
        style={{
          color: 'var(--color-text-secondary)',
          background: 'var(--color-bg-input)',
          borderColor: 'var(--color-border)',
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Change color theme"
      >
        <span
          className="w-3.5 h-3.5 rounded-full border-2"
          style={{
            background: active.swatch[2],
            borderColor: 'var(--color-bg)',
            boxShadow: '0 0 0 1.5px var(--color-border-light)',
          }}
        />
        <span className="hidden sm:inline" style={{ color: 'var(--color-text)' }}>
          {active.label}
        </span>
        <ChevronDown className="w-3.5 h-3.5" style={{ color: 'var(--color-text-muted)' }} />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Color theme"
          className="absolute right-0 top-11 w-64 p-1.5 rounded-xl border shadow-2xl z-50"
          style={{ background: 'var(--color-bg-card)', borderColor: 'var(--color-border)' }}
        >
          <div
            className="px-2.5 py-2 text-[9.5px] font-mono uppercase tracking-[.14em]"
            style={{ color: 'var(--color-text-muted)' }}
          >
            Color theme
          </div>
          {THEMES.map((t) => {
            const sel = t.id === theme
            return (
              <button
                key={t.id}
                type="button"
                role="option"
                aria-selected={sel}
                onClick={() => {
                  setTheme(t.id)
                  setOpen(false)
                }}
                className="w-full flex items-center gap-3 px-2.5 py-2 rounded-lg text-left transition-colors"
                style={{ background: sel ? 'var(--color-accent-muted)' : 'transparent' }}
                onMouseEnter={(e) => {
                  if (!sel) e.currentTarget.style.background = 'var(--color-bg-hover)'
                }}
                onMouseLeave={(e) => {
                  if (!sel) e.currentTarget.style.background = 'transparent'
                }}
              >
                <span
                  className="flex w-8 h-8 rounded-lg overflow-hidden flex-none border"
                  style={{ borderColor: 'var(--color-border)' }}
                >
                  {t.swatch.map((c, i) => (
                    <span key={i} className="flex-1" style={{ background: c }} />
                  ))}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-[13px] font-semibold" style={{ color: 'var(--color-text)' }}>
                    {t.label}
                  </span>
                  <span className="block text-[11px]" style={{ color: 'var(--color-text-muted)' }}>
                    {t.hint}
                  </span>
                </span>
                {sel && <Check className="w-4 h-4 flex-none" style={{ color: 'var(--color-accent)' }} />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
