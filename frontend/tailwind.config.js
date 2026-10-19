/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      // Colours live in src/index.css as theme tokens, written `bg-[var(--…)]`
      // at the call site (W29a UIX-13 deleted the unused colour map and its
      // teal brand scale here: no class read them).
      //
      // The two type-scale sizes the default scale lacks (W29a UIX-04a): 11 px
      // for dense tables and chips, 16 px for lead text. Written as the app's
      // own tokens (index.css :root --text-2xs / --text-md): under @config,
      // Tailwind v4 INLINES a JS-config size into the utility, so a literal
      // '11px' here would never follow presentation mode's --text-2xs: 16px.
      // The line-heights are ratios: 16 px at the desk sizes, and they grow
      // with the size.
      fontSize: {
        '2xs': ['var(--text-2xs)', { lineHeight: 'calc(16 / 11)' }],
        md: ['var(--text-md)', { lineHeight: '1.5' }],
      },
      // Point Tailwind's font utilities at the per-theme CSS vars so switching
      // theme swaps the type system too (each [data-theme] block sets --font-*).
      // Fallbacks after the CSS var are system stacks only. They used to name
      // 'Sora' and 'IBM Plex Mono', which the 2026-08 retheme stopped loading —
      // so those entries could never resolve except on a machine that happened
      // to have the font installed locally, which is a rendering difference
      // between developers rather than a fallback.
      fontFamily: {
        display: ['var(--font-display)', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        sans: ['var(--font-sans)', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
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
