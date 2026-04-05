import { Moon, Sun } from 'lucide-react'
import { useThemeStore } from '@/store/themeStore'

export default function ThemeToggle() {
  const { theme, toggle } = useThemeStore()
  const isMidnight = theme === 'midnight'

  return (
    <button
      onClick={toggle}
      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm transition-colors"
      style={{
        color: 'var(--color-text-muted)',
        background: 'transparent',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.color = 'var(--color-text)'
        e.currentTarget.style.background = 'var(--color-accent-muted)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.color = 'var(--color-text-muted)'
        e.currentTarget.style.background = 'transparent'
      }}
      title={isMidnight ? 'Switch to Classic theme' : 'Switch to Midnight theme'}
      aria-label={`Current theme: ${isMidnight ? 'Midnight' : 'Classic'}. Click to switch.`}
    >
      {isMidnight
        ? <Sun className="w-4 h-4" />
        : <Moon className="w-4 h-4" />
      }
      <span className="hidden sm:inline text-xs font-medium">
        {isMidnight ? 'Classic' : 'Midnight'}
      </span>
    </button>
  )
}
