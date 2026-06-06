interface AppLogoProps {
  /** Renders the square `[t]` glyph instead of the full wordmark. Use for
   *  collapsed sidebar / small surfaces. */
  glyph?: boolean
  /** Light-canvas variant — for marketing pages. Default is dark. */
  light?: boolean
  className?: string
}

/**
 * Bracketed wordmark — TestLookup's canonical mark.
 *   [testlookup]   ← brackets in JetBrains Mono / accent blue, wordmark in Inter
 *
 * Rendered inline so it inherits theme color and scales with font-size.
 * No PNG, no fallback — the recipe IS the mark.
 *
 * Default-export so the existing call sites (Sidebar, LoginPage,
 * ResetPasswordPage, ChatPage) keep their `import AppLogo from ...` line.
 */
export default function AppLogo({ glyph = false, light = false, className = '' }: AppLogoProps) {
  // Theme-aware: the bracket follows the active accent, the wordmark follows the
  // active text color — so the mark recolors with every theme (lime on Signal,
  // violet on Console, cobalt on light Lab…) with no per-theme edits. The
  // `light` prop is no longer needed — the active theme already encodes it.
  void light
  const bracketColor = 'var(--color-accent)'
  const wordColor    = 'var(--color-text)'

  if (glyph) {
    return (
      <span
        className={`inline-flex items-baseline leading-none ${className}`}
        role="img"
        aria-label="testlookup"
      >
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 500, color: bracketColor, fontSize: '1.4em' }}>[</span>
        <span style={{ fontFamily: 'Inter, sans-serif', fontWeight: 700, color: wordColor, letterSpacing: '-0.02em' }}>t</span>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 500, color: bracketColor, fontSize: '1.4em' }}>]</span>
      </span>
    )
  }

  return (
    <span
      className={`inline-flex items-baseline leading-none ${className}`}
      role="img"
      aria-label="testlookup"
    >
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 500, color: bracketColor, fontSize: '1.45em' }}>[</span>
      <span style={{ fontFamily: 'Inter, sans-serif', fontWeight: 400, color: wordColor, letterSpacing: '-0.02em' }}>
        test<b style={{ fontWeight: 700 }}>lookup</b>
      </span>
      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 500, color: bracketColor, fontSize: '1.45em' }}>]</span>
    </span>
  )
}
