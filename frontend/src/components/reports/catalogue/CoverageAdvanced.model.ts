/** The map section's drawn height, px (frame, plot, action row, legend, footer): its placeholder holds it. */
export const COVERAGE_MAP_SECTION_HEIGHT = 600
/** The heatmap section's own placeholder height (`HeatmapSection`: 320 + 150), held while its chunk loads. */
export const COVERAGE_HEATMAP_SECTION_HEIGHT = 470
/** Both sections, the 14 px grid gap between them and the block's 14 px top margin: held while the sections module loads. */
export const COVERAGE_ADVANCED_HEIGHT = 14 + COVERAGE_MAP_SECTION_HEIGHT + 14 + COVERAGE_HEATMAP_SECTION_HEIGHT
