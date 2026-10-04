/**
 * The 3D test scatter, drawn with three.js (VIZ-508; ADR decision 3). The
 * ONLY module that value-imports three, and only through `load.ts`'s dynamic
 * import, so three is one lazy chunk that a reader fetches by pressing
 * "View in 3D" (`check:bundle` fails if it ever reaches an eager chunk).
 *
 * What it draws: the unit cube `scatter3DLayout` places every test in (moved
 * to the origin), its edges, the gridlines on its back walls, a tick mark
 * per labelled value, one `Points` cloud per quadrant (the 2D chart's colour
 * AND shape: each shape is a sprite drawn in 'white' on a 2D canvas and used
 * as an alpha mask, so the colour is the material's), and DOM labels
 * (`CSS2DRenderer`) for the ticks and axis titles.
 *
 * Rules it keeps:
 *   - colours come from tokens only (`tokenColor`), never a numeric literal;
 *   - text reaches the DOM as `textContent` only: a label is a test-free
 *     axis word today, but the rule is the kit's;
 *   - rendering is on demand (the controls' `change`, a resize, new data or
 *     colours): no animation loop burns a frame while nothing moves;
 *   - no zoom and no pan: a wheel over the plot scrolls the page, as it does
 *     over every other chart, and "Reset view" brings the camera back;
 *   - `dispose()` frees every geometry, material and texture, the controls,
 *     the renderer AND its context (browsers cap live WebGL contexts per
 *     page and drop the oldest), then removes its DOM.
 *
 * A browser without WebGL 2 makes `new WebGLRenderer` throw; the caller
 * treats any throw from `mountScatter3D` as "no WebGL".
 */
import {
  BufferGeometry,
  CanvasTexture,
  Float32BufferAttribute,
  LineBasicMaterial,
  LineSegments,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Scene,
  Vector3,
  WebGLRenderer,
  type Material,
  type Object3D,
  type Texture,
} from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import { QUADRANTS, type Quadrant } from '../../testScatter.model'
import { QUADRANT_SYMBOLS, SCATTER_ALPHA } from '../echarts/scatterOption'
import type {
  Scatter3DAxis,
  Scatter3DCallbacks,
  Scatter3DColors,
  Scatter3DHandle,
  Scatter3DLayout,
} from '../../scatter3d.model'
import { tokenColor } from './color'

/** The camera's field of view, degrees, and its distance from what it orbits: the whole cube and its labels in view. */
const FOV = 35
const DISTANCE = 3
/**
 * What the camera orbits: a little below the cube's centre, so the cube sits
 * high in the plot and the two axes along its floor keep room for their tick
 * labels and titles under it.
 */
const TARGET_Y = -0.14
/** The starting view: from the front right, a little above (degrees). */
const START_AZIMUTH = 35
const START_ELEVATION = 22
/** How far the camera may tip over the top or under the floor (radians from straight up or down). */
const POLAR_MARGIN = 0.12
/** A pointer this close to a point's edge still picks it, CSS px. */
const PICK_SLOP = 3
/** Tick marks and labels stand off the cube by these, in cube units. */
const TICK_LENGTH = 0.03
const TICK_LABEL_OFFSET = 0.09
const TITLE_OFFSET = 0.24
const TITLE_ABOVE = 0.12
/** The sprite canvas each point shape is drawn on, px. */
const SPRITE_SIZE = 64

const toRadians = (degrees: number) => (degrees * Math.PI) / 180

type Vec3 = [number, number, number]

/**
 * Layout space (0..1 on each axis) to the scene's cube, centred on the
 * origin. Executions run AWAY from the viewer, so the corner nearest the
 * starting camera holds the longest duration and the fewest executions —
 * two labels that never sit on each other there.
 */
const toScene = (x: number, y: number, z: number): Vec3 => [x - 0.5, y - 0.5, 0.5 - z]

/** One shape (the 2D chart's symbol), in 'white' on a transparent canvas: the material's alpha mask. */
function spriteTexture(shape: (typeof QUADRANT_SYMBOLS)[Quadrant]): Texture {
  const canvas = document.createElement('canvas')
  canvas.width = SPRITE_SIZE
  canvas.height = SPRITE_SIZE
  const ctx = canvas.getContext('2d')
  if (ctx) {
    const s = SPRITE_SIZE
    ctx.fillStyle = 'white'
    ctx.beginPath()
    switch (shape) {
      case 'triangle':
        ctx.moveTo(s / 2, s * 0.06)
        ctx.lineTo(s * 0.94, s * 0.9)
        ctx.lineTo(s * 0.06, s * 0.9)
        break
      case 'diamond':
        ctx.moveTo(s / 2, s * 0.03)
        ctx.lineTo(s * 0.97, s / 2)
        ctx.lineTo(s / 2, s * 0.97)
        ctx.lineTo(s * 0.03, s / 2)
        break
      case 'rect':
        ctx.rect(s * 0.12, s * 0.12, s * 0.76, s * 0.76)
        break
      case 'circle':
        ctx.arc(s / 2, s / 2, s * 0.44, 0, Math.PI * 2)
        break
    }
    ctx.closePath()
    ctx.fill()
  }
  return new CanvasTexture(canvas)
}

/** A DOM label at a point of the cube: `textContent` only, coloured by a CSS variable (it follows the theme on its own). */
function label(text: string, at: Vec3, kind: 'tick' | 'title'): CSS2DObject {
  const el = document.createElement('div')
  el.textContent = text
  el.setAttribute(kind === 'title' ? 'data-scatter-3d-title' : 'data-scatter-3d-tick', '')
  el.style.pointerEvents = 'none'
  el.style.whiteSpace = 'nowrap'
  el.style.fontSize = kind === 'title' ? '12px' : '11px'
  el.style.color = kind === 'title' ? 'var(--color-text-secondary)' : 'var(--chart-axis)'
  const object = new CSS2DObject(el)
  object.position.set(...at)
  return object
}

/**
 * The axes in scene space: where each runs, which way its ticks and labels
 * stand off the cube, and where its title goes (the vertical axis' above its
 * top, where no tick label is).
 */
const AXES: { key: 'x' | 'y' | 'z'; along: (t: number) => Vec3; out: Vec3; title: () => Vec3 }[] = [
  // Across the bottom front edge.
  { key: 'x', along: (t) => toScene(t, 0, 0), out: [0, -0.55, 0.85], title: () => offset(toScene(0.5, 0, 0), [0, -0.55, 0.85], TITLE_OFFSET) },
  // Up the front left edge.
  { key: 'y', along: (t) => toScene(0, t, 0), out: [-0.85, 0, 0.55], title: () => offset(toScene(0, 1, 0), [0, 1, 0], TITLE_ABOVE) },
  // Back along the bottom right edge.
  { key: 'z', along: (t) => toScene(1, 0, t), out: [0.95, -0.3, 0], title: () => offset(toScene(1, 0, 0.5), [0.95, -0.3, 0], TITLE_OFFSET) },
]

function offset(p: Vec3, dir: Vec3, by: number): Vec3 {
  return [
    p[0] + dir[0] * by,
    p[1] + dir[1] * by,
    p[2] + dir[2] * by,
  ]
}

/** The 12 edges of the cube centred on the origin, as segment endpoints. */
function cubeEdges(): number[] {
  const out: number[] = []
  const h = 0.5
  for (const a of [-h, h]) {
    for (const b of [-h, h]) {
      out.push(-h, a, b, h, a, b) // along x
      out.push(a, -h, b, a, h, b) // along y
      out.push(a, b, -h, a, b, h) // along z
    }
  }
  return out
}

export function mountScatter3D(
  host: HTMLElement,
  initialLayout: Scatter3DLayout,
  initialColors: Scatter3DColors,
  callbacks: Scatter3DCallbacks,
): Scatter3DHandle {
  // Throws without WebGL 2: the caller's "no WebGL".
  const renderer = new WebGLRenderer({ antialias: true, alpha: true })
  try {
    return draw(renderer, host, initialLayout, initialColors, callbacks)
  } catch (error) {
    // A half-built view still holds a context: give it back before reporting.
    renderer.dispose()
    renderer.forceContextLoss()
    renderer.domElement.remove()
    throw error
  }
}

function draw(
  renderer: WebGLRenderer,
  host: HTMLElement,
  initialLayout: Scatter3DLayout,
  initialColors: Scatter3DColors,
  callbacks: Scatter3DCallbacks,
): Scatter3DHandle {
  const size = () => ({ width: Math.max(1, host.clientWidth), height: Math.max(1, host.clientHeight) })
  let { width, height } = size()

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(width, height)
  const canvas = renderer.domElement
  canvas.style.display = 'block'
  host.appendChild(canvas)

  const labels = new CSS2DRenderer()
  labels.setSize(width, height)
  const labelLayer = labels.domElement
  labelLayer.style.position = 'absolute'
  labelLayer.style.top = '0'
  labelLayer.style.left = '0'
  labelLayer.style.pointerEvents = 'none'
  // Its own stacking context: the labels' depth-sorted z-indexes stay under the host's tooltip.
  labelLayer.style.zIndex = '0'
  host.appendChild(labelLayer)

  const scene = new Scene()
  const camera = new PerspectiveCamera(FOV, width / height, 0.1, 50)
  const elevation = toRadians(START_ELEVATION)
  const azimuth = toRadians(START_AZIMUTH)
  camera.position.set(
    DISTANCE * Math.cos(elevation) * Math.sin(azimuth),
    TARGET_Y + DISTANCE * Math.sin(elevation),
    DISTANCE * Math.cos(elevation) * Math.cos(azimuth),
  )

  // Mouse and one-finger touch rotate; nothing else (no keys, no wheel, no pan).
  const controls = new OrbitControls(camera, host)
  controls.enableZoom = false
  controls.enablePan = false
  controls.enableDamping = false
  controls.minPolarAngle = POLAR_MARGIN
  controls.maxPolarAngle = Math.PI - POLAR_MARGIN
  controls.target.set(0, TARGET_Y, 0)
  controls.update()
  controls.saveState()

  let disposed = false
  let lost = false
  let layout = initialLayout

  const render = () => {
    if (disposed || lost) return
    renderer.render(scene, camera)
    labels.render(scene, camera)
  }
  const reportCamera = () => callbacks.onCameraChange?.(Math.round((controls.getAzimuthalAngle() * 180) / Math.PI))
  const onChange = () => {
    render()
    reportCamera()
  }
  controls.addEventListener('change', onChange)

  const onContextLost = () => {
    lost = true
    callbacks.onContextLost()
  }
  canvas.addEventListener('webglcontextlost', onContextLost)

  // ── The frame: edges, back-wall gridlines, tick marks ──────────────────────
  const sprites = new Map(QUADRANTS.map((q) => [q, spriteTexture(QUADRANT_SYMBOLS[q])]))
  const frameMaterial = new LineBasicMaterial()
  const gridMaterial = new LineBasicMaterial()
  const pointMaterials = new Map(
    QUADRANTS.map((q) => [
      q,
      new PointsMaterial({
        size: layout.pointSize,
        sizeAttenuation: false,
        alphaMap: sprites.get(q),
        transparent: true,
        opacity: SCATTER_ALPHA,
        alphaTest: 0.05,
        depthWrite: false,
      }),
    ]),
  )

  // What `update` rebuilds: the geometry-bearing objects and the labels.
  let drawn: Object3D[] = []
  let labelObjects: CSS2DObject[] = []

  const clearDrawn = () => {
    for (const object of drawn) {
      scene.remove(object)
      ;(object as LineSegments | Points).geometry.dispose()
    }
    for (const object of labelObjects) object.removeFromParent()
    drawn = []
    labelObjects = []
  }

  const lines = (positions: number[], material: LineBasicMaterial) => {
    const geometry = new BufferGeometry()
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3))
    const segments = new LineSegments(geometry, material)
    scene.add(segments)
    drawn.push(segments)
  }

  const build = () => {
    clearDrawn()
    const ticks: number[] = []
    const grid: number[] = []
    for (const axis of AXES) {
      const spec: Scatter3DAxis = layout[axis.key]
      for (const tick of spec.ticks) {
        const at = axis.along(tick.at)
        ticks.push(...at, ...offset(at, axis.out, TICK_LENGTH))
        const text = label(tick.text, offset(at, axis.out, TICK_LABEL_OFFSET), 'tick')
        scene.add(text)
        labelObjects.push(text)
      }
      const title = label(spec.title, axis.title(), 'title')
      scene.add(title)
      labelObjects.push(title)
    }
    // Failure-rate gridlines on the two back walls, where they never cross a point's front.
    for (const tick of layout.y.ticks) {
      const y = toScene(0, tick.at, 0)[1]
      grid.push(-0.5, y, -0.5, 0.5, y, -0.5)
      grid.push(-0.5, y, -0.5, -0.5, y, 0.5)
    }
    lines(cubeEdges(), gridMaterial)
    lines(grid, gridMaterial)
    lines(ticks, frameMaterial)

    for (const q of QUADRANTS) {
      const indices = layout.groups[q]
      if (indices.length === 0) continue
      const positions: number[] = []
      for (const i of indices) {
        positions.push(...toScene(layout.positions[i * 3], layout.positions[i * 3 + 1], layout.positions[i * 3 + 2]))
      }
      const geometry = new BufferGeometry()
      geometry.setAttribute('position', new Float32BufferAttribute(positions, 3))
      const material = pointMaterials.get(q) as PointsMaterial
      material.size = layout.pointSize
      const cloud = new Points(geometry, material)
      scene.add(cloud)
      drawn.push(cloud)
    }
  }

  const paint = (colors: Scatter3DColors) => {
    frameMaterial.color.copy(tokenColor(colors.frame))
    gridMaterial.color.copy(tokenColor(colors.grid))
    for (const q of QUADRANTS) (pointMaterials.get(q) as PointsMaterial).color.copy(tokenColor(colors.quadrants[q]))
  }

  build()
  paint(initialColors)
  render()
  reportCamera()

  const point = new Vector3()

  return {
    update(next) {
      if (disposed) return
      layout = next
      build()
      render()
    },
    setColors(colors) {
      if (disposed) return
      paint(colors)
      render()
    },
    resize() {
      if (disposed) return
      ;({ width, height } = size())
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
      labels.setSize(width, height)
      render()
    },
    pick(x, y) {
      if (disposed || lost) return null
      camera.updateMatrixWorld()
      const radius = layout.pointSize / 2 + PICK_SLOP
      let best: number | null = null
      let bestDepth = Infinity
      const count = layout.positions.length / 3
      for (let i = 0; i < count; i++) {
        point.set(...toScene(layout.positions[i * 3], layout.positions[i * 3 + 1], layout.positions[i * 3 + 2])).project(camera)
        if (point.z < -1 || point.z > 1) continue
        const dx = ((point.x + 1) / 2) * width - x
        const dy = ((1 - point.y) / 2) * height - y
        // The front-most point under the pointer: the one drawn over the others.
        if (dx * dx + dy * dy <= radius * radius && point.z < bestDepth) {
          best = i
          bestDepth = point.z
        }
      }
      return best
    },
    resetView() {
      if (disposed) return
      controls.reset()
    },
    dispose() {
      if (disposed) return
      disposed = true
      controls.removeEventListener('change', onChange)
      controls.dispose()
      canvas.removeEventListener('webglcontextlost', onContextLost)
      clearDrawn()
      const materials: Material[] = [frameMaterial, gridMaterial, ...pointMaterials.values()]
      for (const material of materials) material.dispose()
      for (const texture of sprites.values()) texture.dispose()
      renderer.dispose()
      // A context already lost has nothing left to give back.
      if (!lost) renderer.forceContextLoss()
      canvas.remove()
      labelLayer.remove()
    },
  }
}
