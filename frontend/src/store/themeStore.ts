import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// ── The registry: add a theme here + a [data-theme] block in index.css. ──
// swatch = [bg, card, accent] — rendered as the three-stripe chip in the picker.
export const THEMES = [
  { id: 'signal', label: 'Signal', hint: 'Charcoal · blue', swatch: ['#101318', '#181d25', '#3b82f6'] },
  { id: 'console', label: 'Console', hint: 'Near-black · indigo', swatch: ['#0b0c10', '#13161c', '#6366f1'] },
  { id: 'slate', label: 'Slate', hint: 'Cool grey · sky', swatch: ['#11151c', '#1b212c', '#5e9ed6'] },
  { id: 'ember', label: 'Ember', hint: 'Warm charcoal · amber', swatch: ['#16120e', '#201a14', '#d08c3a'] },
  { id: 'lab', label: 'Lab', hint: 'Light · blue', swatch: ['#f5f6f8', '#ffffff', '#2563eb'] },
  { id: 'midnight', label: 'Midnight', hint: 'Classic GitHub dark', swatch: ['#0d1117', '#151b23', '#3b82f6'] },
] as const

export type ThemeId = (typeof THEMES)[number]['id']

const DEFAULT_THEME: ThemeId = 'signal'
const isValid = (t: string): t is ThemeId => THEMES.some((x) => x.id === t)

interface ThemeState {
  theme: ThemeId
  setTheme: (t: ThemeId) => void
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: DEFAULT_THEME,
      setTheme: (theme) => {
        set({ theme })
        applyTheme(theme)
      },
    }),
    {
      name: 'testlookup-theme',
      // Migrate any persisted value that's no longer a valid id (e.g. the old
      // 'classic' theme, removed in the multi-theme migration) to the default,
      // so a stale localStorage value can't set an undefined data-theme.
      merge: (persisted, current) => {
        const p = persisted as Partial<ThemeState> | undefined
        const theme = p?.theme && isValid(p.theme) ? p.theme : DEFAULT_THEME
        return { ...current, ...p, theme }
      },
    },
  ),
)

function applyTheme(theme: ThemeId) {
  document.documentElement.setAttribute('data-theme', theme)
}

// Apply on first load (this module is imported early), before first paint.
applyTheme(useThemeStore.getState().theme)
