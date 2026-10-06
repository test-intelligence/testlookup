import { THEMES, useThemeStore } from '@/store/themeStore'

/**
 * The color-theme picker: one swatch per theme in the registry, inline in the
 * account menu (UX redesign P1; it was a dropdown of its own in the top bar).
 * The active theme's name is written beside the row, and each swatch names its
 * theme and hint on hover. Persistence and first-paint apply live in the store.
 */
export default function ThemePicker() {
  const theme = useThemeStore((s) => s.theme)
  const setTheme = useThemeStore((s) => s.setTheme)
  const active = THEMES.find((t) => t.id === theme) ?? THEMES[0]

  return (
    <div data-theme-picker="">
      <div className="mb-1.5 flex items-baseline justify-between text-[11px]">
        <span className="font-medium uppercase tracking-wider text-[var(--color-text-muted)]">Theme</span>
        <span className="text-[var(--color-text-secondary)]">{active.label}</span>
      </div>
      <div role="listbox" aria-label="Color theme" className="flex gap-1.5">
        {THEMES.map((t) => {
          const sel = t.id === theme
          return (
            <button
              key={t.id}
              type="button"
              role="option"
              aria-selected={sel}
              aria-label={t.label}
              title={`${t.label}: ${t.hint}`}
              onClick={() => setTheme(t.id)}
              className="flex h-7 w-7 overflow-hidden rounded-md border-2 transition-transform hover:scale-105"
              style={{ borderColor: sel ? 'var(--color-accent)' : 'var(--color-border)' }}
            >
              {t.swatch.map((c, i) => (
                <span key={i} className="flex-1" style={{ background: c }} />
              ))}
            </button>
          )
        })}
      </div>
    </div>
  )
}
