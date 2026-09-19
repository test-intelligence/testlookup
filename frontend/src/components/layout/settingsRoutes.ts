/**
 * Route predicates for the settings section.
 *
 * Separate from `SettingsBackBar.tsx` because exporting a helper alongside a
 * component breaks React fast refresh (`react-refresh/only-export-components`).
 */

/** The settings index — the destination, not a sub-page. */
export const SETTINGS_ROOT = '/settings'

/**
 * True for `/settings/<something>`, false for `/settings` and everything else.
 *
 * A naive `pathname.startsWith('/settings')` would also match `/settingsfoo`,
 * so the separator is required explicitly.
 */
export function isSettingsSubPage(pathname: string): boolean {
  if (!pathname.startsWith(`${SETTINGS_ROOT}/`)) return false
  const rest = pathname.slice(SETTINGS_ROOT.length + 1)
  return rest.length > 0 && rest !== '/'
}
