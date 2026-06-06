/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f0fdfa',
          100: '#ccfbf1',
          400: '#2dd4bf',
          500: '#14b8a6',
          600: '#0d9488',
          700: '#0f766e',
          900: '#134e4a',
        },
        bg: {
          DEFAULT: 'var(--color-bg)',
          secondary: 'var(--color-bg-secondary)',
          card: 'var(--color-bg-card)',
          input: 'var(--color-bg-input)',
          hover: 'var(--color-bg-hover)',
        },
        border: {
          DEFAULT: 'var(--color-border)',
          light: 'var(--color-border-light)',
        },
        fg: {
          DEFAULT: 'var(--color-text)',
          secondary: 'var(--color-text-secondary)',
          muted: 'var(--color-text-muted)',
          faint: 'var(--color-text-faint)',
        },
        accent: {
          DEFAULT: 'var(--color-accent)',
          muted: 'var(--color-accent-muted)',
          hover: 'var(--color-accent-hover)',
        },
        cta: {
          DEFAULT: 'var(--color-btn-primary-bg)',
          hover: 'var(--color-btn-primary-hover)',
        },
        sidebar: {
          'active-bg': 'var(--color-sidebar-active-bg)',
          'active-text': 'var(--color-sidebar-active-text)',
        },
        ring: 'var(--color-ring)',
        passed: 'var(--status-passed)',
        failed: 'var(--status-failed)',
        broken: 'var(--status-broken)',
        skipped: 'var(--status-skipped)',
        flaky: 'var(--status-flaky)',
        gate: {
          go: 'var(--gate-go)',
          conditional: 'var(--gate-conditional)',
          'no-go': 'var(--gate-no-go)',
        },
      },
      fontFamily: {
        display: ['Sora', 'system-ui', 'sans-serif'],
        sans: ['Plus Jakarta Sans', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['IBM Plex Mono', 'JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        xl: 'var(--shadow-xl)',
      },
    },
  },
  plugins: [],
}
