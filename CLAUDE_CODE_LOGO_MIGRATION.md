# TestLookup — Logo Migration Handoff (Option E)

**For:** Claude Code, working in the `anandtopu/testlookup` repo.
**Goal:** Replace the existing PNG logo with the new bracketed wordmark (option E) across the entire frontend.

---

## 1. What's changing

**Old:** `frontend/public/testLookup_Logo.png` — black rounded-rect with white wordmark, plus a teal-gradient text fallback in `AppLogo.tsx`.

**New:** A typographic recipe rendered inline as SVG (or HTML for the CLI):
- **Brackets** — JetBrains Mono, weight 500, color `#4493f8` (on dark) / `#2563eb` (on light), sized 1.5× the wordmark cap-height
- **Wordmark** — Inter, "test" weight 400 + "lookup" weight 700, color `#f0f6fc` (on dark) / `#0d1117` (on light), letter-spacing `-0.02em`
- **Glyph** (for favicon, app icon, sidebar collapsed): same brackets + a single bold lowercase `t` in the middle

No more PNG. No more teal-gradient fallback.

---

## 2. Files to add

Create `frontend/src/assets/brand/` with these five SVGs (copy verbatim):

### `frontend/src/assets/brand/wordmark-dark.svg`
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 268 56" role="img" aria-label="testlookup">
  <text x="2"   y="42" font-family="JetBrains Mono, monospace" font-size="48" font-weight="500" fill="#4493f8">[</text>
  <text x="32"  y="40" font-family="Inter, sans-serif" font-size="32" font-weight="400" fill="#f0f6fc" letter-spacing="-0.02em">test<tspan font-weight="700">lookup</tspan></text>
  <text x="234" y="42" font-family="JetBrains Mono, monospace" font-size="48" font-weight="500" fill="#4493f8">]</text>
</svg>
```

### `frontend/src/assets/brand/wordmark-light.svg`
Same as above but `#4493f8` → `#2563eb` and `#f0f6fc` → `#0d1117`.

### `frontend/src/assets/brand/glyph-dark.svg`
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="testlookup">
  <text x="6"  y="50" font-family="JetBrains Mono, monospace" font-size="56" font-weight="500" fill="#4493f8">[</text>
  <text x="22" y="48" font-family="Inter, sans-serif" font-size="44" font-weight="700" fill="#f0f6fc" letter-spacing="-0.02em">t</text>
  <text x="42" y="50" font-family="JetBrains Mono, monospace" font-size="56" font-weight="500" fill="#4493f8">]</text>
</svg>
```

### `frontend/public/favicon.svg`
Same content as `glyph-dark.svg`.

### `frontend/public/app-icon.svg`
Same content as `glyph-dark.svg` but wrapped in a rounded-square plate:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="0" y="0" width="64" height="64" rx="14" fill="#0d1117"/>
  <text x="6"  y="50" font-family="JetBrains Mono, monospace" font-size="56" font-weight="500" fill="#4493f8">[</text>
  <text x="22" y="48" font-family="Inter, sans-serif" font-size="44" font-weight="700" fill="#f0f6fc" letter-spacing="-0.02em">t</text>
  <text x="42" y="50" font-family="JetBrains Mono, monospace" font-size="56" font-weight="500" fill="#4493f8">]</text>
</svg>
```

---

## 3. Replace `AppLogo.tsx`

**File:** `frontend/src/components/ui/AppLogo.tsx`

Replace the entire file with:

```tsx
interface AppLogoProps {
  /** Renders the square `[t]` glyph instead of the full wordmark. Use for collapsed sidebar / small surfaces. */
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
 */
export function AppLogo({ glyph = false, light = false, className = '' }: AppLogoProps) {
  const bracketColor = light ? '#2563eb' : '#4493f8'
  const wordColor    = light ? '#0d1117' : '#f0f6fc'

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
```

---

## 4. Update sidebar

**File:** `frontend/src/components/layout/Sidebar.tsx`

Find the existing logo block at the top — likely something like `<img src="/testLookup_Logo.png" .. />` or `<AppLogo />` rendered with `h-12` — and replace it with:

```tsx
<div className="px-4 py-4 border-b border-[var(--color-border)]">
  <AppLogo className="text-[20px]" />
</div>
```

The wordmark now scales with `font-size`, so `text-[20px]` controls the whole thing.

For a future collapsed-sidebar state, swap to `<AppLogo glyph className="text-[24px]" />`.

---

## 5. Wire up the favicon + meta tags

**File:** `frontend/index.html`

Replace the existing favicon `<link>` (currently points to `/testLookup_Logo.png`) with:

```html
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<link rel="apple-touch-icon" href="/app-icon.svg" />
```

Optional but recommended — also update the `<meta>` description and OG tags so they match the new identity. If there's existing OG image meta pointing to the old PNG, leave a TODO comment for the design team to generate a 1200×628 PNG from the OG card layout in the design system.

---

## 6. Retire the old assets

After CI passes:

```bash
git mv frontend/public/testLookup_Logo.png frontend/public/_legacy/testLookup_Logo.png
```

(Or just `rm` it — the design system has a copy archived.)

Search the repo for any remaining references:

```bash
rg "testLookup_Logo|AppLogo.*fallback" frontend/
```

Anything that turns up should be deleted or migrated to the new `<AppLogo />` API.

---

## 7. CLI banner (separate, lower priority)

**Repo location:** wherever the CLI's `--version` / startup banner is printed (likely `cli/src/banner.ts` or similar — grep for `testlookup` ASCII art).

Replace the banner with:

```ts
const RESET = '\x1b[0m'
const BLUE  = '\x1b[38;5;75m'   // approx #4493f8
const BOLD  = '\x1b[1m'
const DIM   = '\x1b[2m'

export const banner = `
  ${BLUE}[${RESET}${BOLD}testlookup${RESET}${BLUE}]${RESET} ${DIM}v${VERSION} · local-first${RESET}
`
```

This makes the CLI splash mirror the GUI mark exactly.

---

## 8. Verification checklist

After the migration, eyeball these in the running app:

- [ ] Sidebar header shows `[testlookup]` with blue brackets, white "test" + bold "lookup"
- [ ] Browser tab favicon shows the `[t]` glyph (not a generic icon, not the old PNG)
- [ ] On macOS, "Add to Dock" / PWA install shows the rounded-square `[t]` plate
- [ ] No 404s in DevTools network tab for `testLookup_Logo.png`
- [ ] `rg testLookup_Logo` returns zero hits in `frontend/src/`
- [ ] Lighthouse passes the icon checks
- [ ] Light-mode marketing pages (if any) render `<AppLogo light />` correctly with dark wordmark + blue brackets

---

## 9. What to ask if blocked

- **Fonts not loading?** Make sure `Inter` and `JetBrains Mono` are imported in `frontend/src/index.css` — they should already be, since the rest of the UI uses them. The wordmark falls back to system mono/sans gracefully but won't be pixel-perfect.
- **Outline-vs-text in production SVG?** Current SVGs use `<text>` elements so they're editable. If brand wants pixel-perfect rendering on machines without Inter installed (rare for a developer tool, but possible), run them through Figma → "Outline strokes" and re-commit.
- **Need a PNG fallback?** Generate `favicon-32.png` via `inkscape favicon.svg --export-filename=favicon-32.png -w 32 -h 32` and add `<link rel="icon" type="image/png" href="/favicon-32.png" />` after the SVG link.

---

## 10. Commit suggestion

```
brand: migrate logo to bracketed wordmark (option E)

- Add SVG assets in src/assets/brand/ + public/favicon.svg + public/app-icon.svg
- Replace AppLogo.tsx with inline typographic recipe (Inter + JetBrains Mono brackets)
- Update Sidebar.tsx to use new <AppLogo />
- Update index.html favicon links to point at SVG glyph
- Update CLI banner to match
- Retire frontend/public/testLookup_Logo.png

The mark is now a typographic recipe, not a frozen image — it inherits
theme color, scales cleanly, and works at every size from 16px favicon
to 128px social avatar. Brand spec lives in the testlookup-design system.
```

That's the whole migration. Should be one PR, ~150 lines net diff.
