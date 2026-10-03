/**
 * Text that ECharts draws on the canvas, made safe for zrender's text caches.
 *
 * zrender measures every drawn string through a plain-object LRU keyed by the
 * text (`zrender/lib/core/LRU.js`: `this._map = {}`). A key that is an
 * `Object.prototype` member name (`__proto__`, `constructor`, `toString`...)
 * reads the prototype (or `Object`) as a cache ENTRY, and the LRU then writes
 * `prev` / `next` onto it: every object in the app gains two enumerable keys
 * (found by B0 and I-G in Wave 3: a test or suite NAMED `__proto__` in an
 * ingested CI file is enough; axe then threw, React logged on every style
 * update, and any `for...in` over a plain object saw the extra keys).
 *
 * Not only the whole text is a key (R1B-1). zrender cuts a label that is too
 * wide for its box (`overflow: 'truncate'`) by measuring PREFIXES of it
 * (`truncateSingleLine`: `measureWidth(textLine.substr(0, n))`), and it
 * measures each `\n` line on its own. So `__proto__bomb_label_long` cut to
 * 61 px put `__proto__` itself into the cache. A prefix of a line equals a
 * member name exactly when the line STARTS WITH one, so such a line is drawn
 * with a leading WORD JOINER (U+2060: zero width, no line break): every
 * prefix zrender can take then starts with the joiner, and none is a member.
 * Every other text is returned unchanged (a canvas that draws no hostile name
 * is byte-identical). Wrapping (`overflow: 'break' | 'breakAll'`) cuts lines
 * at other places and is NOT covered: no option builder may use it
 * (`canvasText.test.ts` checks every builder's source).
 *
 * Only the CANVAS copy changes: tooltips, tables, the keyboard readout and the
 * rows panel keep the name as given (React text). Event handlers read cells
 * by index, never by the drawn name.
 */
const WORD_JOINER = String.fromCharCode(0x2060)

/**
 * The member names read at call time, not at load: anything that later adds a
 * member to `Object.prototype` (a polyfill) is a key to avoid too.
 */
function memberNames(): string[] {
  return Object.getOwnPropertyNames(Object.prototype)
}

function safeLine(line: string, members: readonly string[]): string {
  return members.some((member) => line.startsWith(member)) ? `${WORD_JOINER}${line}` : line
}

/**
 * `text` as the canvas may draw it: each line that starts with an
 * `Object.prototype` member name gets a leading word joiner; anything else is
 * returned as given.
 */
export function canvasSafeText(text: string): string {
  const members = memberNames()
  if (!text.includes('\n')) return safeLine(text, members)
  return text
    .split('\n')
    .map((line) => safeLine(line, members))
    .join('\n')
}

/** Every label of a category axis, made canvas-safe (a new array; the input is not touched). */
export function canvasSafeLabels(labels: readonly string[]): string[] {
  return labels.map(canvasSafeText)
}
