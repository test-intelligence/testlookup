/**
 * The Mermaid configuration the documentation renders with.
 *
 * This is a separate module so that exactly one configuration exists. The CI
 * check that measures whether diagram labels fit inside their boxes renders
 * the guide's diagrams in a harness rather than in the app, and a harness that
 * configured Mermaid its own way would be testing something the reader never
 * sees. Both import from here.
 *
 * Split into "read the palette" and "build the config" because only the first
 * half needs a live document: the CI spec builds the config in Node and hands
 * the plain object to the browser.
 */
import type { MermaidConfig } from 'mermaid'

export interface DiagramPalette {
  text: string
  muted: string
  accent: string
  surface: string
  border: string
  font: string
}

/** Reads a CSS custom property, falling back when there is no document. */
export function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/**
 * The active theme's colours. The app ships six themes and the CSS custom
 * properties are the single source for them, so the diagram reads the same
 * variables every other component does rather than carrying a second palette
 * that would drift.
 */
export function readDiagramPalette(): DiagramPalette {
  return {
    text: cssVar('--color-text', '#e6e6e6'),
    muted: cssVar('--color-text-muted', '#9aa0a6'),
    accent: cssVar('--color-accent', '#3b82f6'),
    surface: cssVar('--color-bg-secondary', '#181d25'),
    border: cssVar('--color-border', '#2a2f38'),
    font: cssVar('--font-sans', 'system-ui, sans-serif'),
  }
}

export function mermaidConfig(palette: DiagramPalette): MermaidConfig {
  const { text, muted, accent, surface, border, font } = palette
  return {
    startOnLoad: false,
    securityLevel: 'strict',
    // 'base' is the only theme that honours themeVariables fully.
    theme: 'base',
    // A CONCRETE stack, never 'inherit'. Mermaid sizes every node box by
    // measuring its label in a detached element; under 'inherit' that element
    // resolves the font differently from the finished SVG, so the boxes come
    // out narrower than the text and every label is clipped ("Backend AP",
    // "MCP serve"). The app uses one sans stack across all six themes, so
    // naming it here costs nothing and keeps measurement and rendering in the
    // same font.
    fontFamily: font,
    themeVariables: {
      background: 'transparent',
      fontFamily: font,
      primaryColor: surface,
      primaryTextColor: text,
      primaryBorderColor: accent,
      secondaryColor: surface,
      tertiaryColor: surface,
      lineColor: muted,
      textColor: text,
      mainBkg: surface,
      nodeBorder: accent,
      clusterBkg: 'transparent',
      clusterBorder: border,
      edgeLabelBackground: surface,
    },
  }
}
