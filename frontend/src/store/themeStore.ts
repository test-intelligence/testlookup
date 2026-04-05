import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type ThemeId = 'midnight' | 'classic'

interface ThemeState {
  theme: ThemeId
  setTheme: (t: ThemeId) => void
  toggle: () => void
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: 'midnight',
      setTheme: (theme) => {
        set({ theme })
        applyTheme(theme)
      },
      toggle: () => {
        const next = get().theme === 'midnight' ? 'classic' : 'midnight'
        set({ theme: next })
        applyTheme(next)
      },
    }),
    { name: 'testlookup-theme' }
  )
)

function applyTheme(theme: ThemeId) {
  document.documentElement.setAttribute('data-theme', theme)
}

// Apply on first load
applyTheme(useThemeStore.getState().theme)
