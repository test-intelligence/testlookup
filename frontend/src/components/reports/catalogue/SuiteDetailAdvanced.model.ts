/**
 * Placeholder heights, px, while a Suite detail section's chunk downloads: the
 * drawn sections' own estimates (a frame's plot plus its chrome), kept here so
 * neither `SuiteDetailAdvanced` nor its lazy sections module imports a section
 * eagerly to read them.
 */
export const SUITE_HEATMAP_MIN_HEIGHT = 470
export const SUITE_SCATTER_MIN_HEIGHT = 580
/** Both sections and the 16 px grid gap between them: the place held while the sections module itself loads. */
export const SUITE_ADVANCED_MIN_HEIGHT = SUITE_HEATMAP_MIN_HEIGHT + 16 + SUITE_SCATTER_MIN_HEIGHT
