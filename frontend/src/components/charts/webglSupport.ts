/**
 * Whether this browser can draw the 3D scatter (VIZ-508), asked BEFORE its
 * engine chunk is fetched: three.js r163+ draws with WebGL 2 only, so a
 * browser without it (WebGL off, a blocklisted GPU, many VMs and remote
 * desktops) gets the 2D chart and a notice, and never downloads the ~135 kB
 * engine it could not run.
 *
 * The probe's context is released at once (`WEBGL_lose_context`): browsers
 * cap live WebGL contexts per page (16 in Chromium) and drop the OLDEST past
 * the cap, so a probe left alive could cost the real view its context.
 * No three here: this module ships with the view, not with the engine.
 */
export function hasWebGL2(): boolean {
  try {
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl2')
    if (!gl) return false
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    return true
  } catch {
    return false
  }
}
