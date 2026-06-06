import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// ── The registry: add a theme here + a [data-theme] block in index.css. ──
// swatch = [bg, card, accent] — rendered as the three-stripe chip in the picker.
export const THEMES = [
  { id: 'signal', label: 'Signal', hint: 'Deep-teal · lime', swatch: ['#0a1411', '#0f1d19', '#b8f24a'] },
  { id: 'console', label: 'Console', hint: 'Near-black · violet', swatch: ['#09090c', '#111118', '#7c5cff'] },
  { id: 'slate', label: 'Slate', hint: 'Cool grey · sky', swatch: ['#11151c', '#1b212c', '#56b6f0'] },
  { id: 'ember', label: 'Ember', hint: 'Warm charcoal · amber', swatch: ['#16110d', '#211913', '#ff9f45'] },
  { id: 'lab', label: 'Lab', hint: 'Light · cobalt', swatch: ['#f4f2ec', '#fbfaf6', '#1f3aff'] },
  { id: 'midnight', label: 'Midnight', hint: 'Classic GitHub dark', swatch: ['#0d1117', '#151b23', '#4493f8'] },
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
