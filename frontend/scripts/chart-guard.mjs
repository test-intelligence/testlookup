/**
 * The chart-engine guards (VIZ-103, ADR decision 4) — imported and run by
 * check-theme-tokens.mjs, so CI and scripts/push_check.py already run them.
 *
 * Two rules, both on a real parse (the TypeScript compiler API), not regex.
 * The regex predecessor missed 7 of 8 bypasses in the 2026-09-22 security
 * review: computed keys, bracket assignment, spreads, Object.assign,
 * Object.defineProperty, async and multi-line methods, and
 * `domTooltipFormatter(r) || (p) => p.name`.
 *
 *  1. IMPORT BOUNDARY. A value import of an engine package (echarts, zrender,
 *     echarts-gl, three — and any deep path under them) is allowed only under
 *     ENGINE_DIRS. `import type` / `export type` / `typeof import(…)` are
 *     erased and stay allowed everywhere. This keeps every ECharts option that
 *     reaches the engine next to the code that checks it, and it closes the
 *     bundle gap where `zrender/lib/core/util` imported eagerly carried none of
 *     the markers check-bundle-budget.mjs looks for.
 *
 *  2. OPTION RULES, over ENGINE_DIRS, src/components/charts/** and every file
 *     that imports from an engines/ module (it may hand the engine an option).
 *     An ECharts tooltip formatter that returns a string is an HTML sink; a
 *     formatter must be written as `formatter: domTooltipFormatter(…)`, the
 *     helper imported from components/charts/tooltip.ts, which returns DOM.
 *     Fails on:
 *       - a property/method/accessor named *formatter (identifier, string or
 *         computed key; sync, async or generator) whose value is not a direct
 *         call to the imported helper;
 *       - `x.formatter = …` / `x['formatter'] = …` unless the value is a helper call;
 *       - `x[k] = …` (non-literal key) into an object named *tooltip/label/legend/axis*;
 *       - Object.assign / Object.defineProperty(ies) / Reflect.set /
 *         Reflect.defineProperty whose target names tooltip/label/legend;
 *       - any other string literal that IS a formatter key ('formatter',
 *         'valueFormatter', …) — defineProperty(tip, 'formatter', …),
 *         Object.fromEntries([['formatter', f]]);
 *       - a spread into an object that is the value of a tooltip/label/legend
 *         key (or a variable of that name), unless the spread is a helper call;
 *       - a `tooltip` value the guard cannot see: anything but an object
 *         literal, an array of them, a same-file `const` bound to one, or a
 *         boolean/null/undefined (`tooltip: sharedTooltip` imported from
 *         elsewhere is the spread bypass without the dots);
 *       - `rich` (ECharts rich-text templates interpret `{a|…}`) unless the
 *         line or the one above carries `chart-guard-allow rich: <reason>`;
 *       - under ENGINE_DIRS only: a computed key that is not a literal
 *         (`{ [K]: fn }` where K = 'format' + 'ter'), unless allow-listed with
 *         `chart-guard-allow computed-key: <reason>`;
 *       - a local declaration that shadows the helper's name.
 *
 * Residual gap (by design, documented in the ADR): an option assembled from
 * innocuously named variables through several files can still smuggle a
 * function in. The complete fix is a runtime check at the one place options
 * reach the engine (engines/useEChart.ts) that rejects any function-valued
 * `formatter` the helper did not produce.
 */
import ts from 'typescript'

/** The ONLY directories (relative to frontend/, forward slashes) allowed to value-import an engine. */
export const ENGINE_DIRS = ['src/components/charts/engines']
/** Engine packages, including deep paths such as `zrender/lib/core/util`. */
export const RESTRICTED_MODULE = /^(?:echarts|zrender|echarts-gl|three)(?:\/.*)?$/
/** The approved DOM tooltip helper and the module it must be imported from. */
export const HELPER_NAME = 'domTooltipFormatter'
const HELPER_MODULE = /(?:^|\/)tooltip(?:\.tsx?)?$/
/** Where the helper is defined — the one file allowed to declare the name. */
export const HELPER_FILE = 'src/components/charts/tooltip.ts'

const FORMATTER_KEY = /formatter$/i
const SINK_OWNER = /tooltip|label|legend/i
const SINK_OWNER_ELEMENT = /tooltip|label|legend|axis/i
const TOOLTIP_KEY = /tooltip/i
const ALLOW = (rule) => new RegExp(`chart-guard-allow\\s+${rule}\\s*:\\s*\\S.{2,}`)

export const inEngineDir = (rel) => ENGINE_DIRS.some((dir) => rel === dir || rel.startsWith(`${dir}/`))

function parse(rel, source) {
  const kind = rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  return ts.createSourceFile(rel, source, ts.ScriptTarget.Latest, true, kind)
}

function lineOf(sf, node) {
  return sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
}

function unwrap(expr) {
  let e = expr
  while (
    e &&
    (ts.isParenthesizedExpression(e) || ts.isAsExpression(e) || ts.isSatisfiesExpression(e) ||
      ts.isNonNullExpression(e) || ts.isTypeAssertionExpression(e))
  ) {
    e = e.expression
  }
  return e
}

function isStringish(node) {
  return node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
}

/**
 * The key of a property-like node: { text } for identifier / string / numeric /
 * literal-computed keys, { dynamic: true } for `[expr]`, null for anything else.
 */
export function keyOf(name) {
  if (!name) return null
  if (ts.isIdentifier(name) || ts.isPrivateIdentifier(name)) return { text: name.text }
  if (isStringish(name) || ts.isNumericLiteral(name)) return { text: name.text }
  if (ts.isComputedPropertyName(name)) {
    const e = unwrap(name.expression)
    if (isStringish(e) || ts.isNumericLiteral(e)) return { text: e.text }
    return { dynamic: true }
  }
  return null
}

// ── Rule 1: import boundary ─────────────────────────────────────────────────
export function importBoundaryViolations(rel, source) {
  if (inEngineDir(rel)) return []
  const sf = parse(rel, source)
  const out = []
  const flag = (node, spec, how) =>
    out.push(
      `${rel}:${lineOf(sf, node)} ${how} '${spec}' — engine packages may be value-imported only under ` +
        `${ENGINE_DIRS.join(', ')} (ADR decision 4); use \`import type\` for types, or reach the engine through ` +
        `loadChartEngine() / useEChart()`,
    )
  const visit = (node) => {
    if (ts.isImportDeclaration(node) && isStringish(node.moduleSpecifier)) {
      const spec = node.moduleSpecifier.text
      if (RESTRICTED_MODULE.test(spec)) {
        const clause = node.importClause
        // Only a whole-clause `import type` is allowed. `import { type A } from 'x'` is
        // flagged too: under verbatimModuleSyntax it is emitted as `import {} from 'x'`,
        // which keeps the module's side effects.
        if (!clause?.isTypeOnly) {
          const allInline =
            clause && !clause.name && clause.namedBindings && ts.isNamedImports(clause.namedBindings) &&
            clause.namedBindings.elements.length > 0 && clause.namedBindings.elements.every((e) => e.isTypeOnly)
          flag(
            node,
            spec,
            allInline ? 'imports only inline `type` names from (write `import type`, which is always erased)' : clause ? 'value-imports' : 'side-effect-imports',
          )
        }
      }
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier && isStringish(node.moduleSpecifier)) {
      const spec = node.moduleSpecifier.text
      if (RESTRICTED_MODULE.test(spec) && !node.isTypeOnly) flag(node, spec, 're-exports')
    } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      const e = node.moduleReference.expression
      if (isStringish(e) && RESTRICTED_MODULE.test(e.text) && !node.isTypeOnly) flag(node, e.text, 'import-requires')
    } else if (ts.isCallExpression(node)) {
      const arg = node.arguments[0]
      const dynamicImport = node.expression.kind === ts.SyntaxKind.ImportKeyword
      const req = ts.isIdentifier(node.expression) && node.expression.text === 'require'
      if ((dynamicImport || req) && isStringish(arg) && RESTRICTED_MODULE.test(arg.text)) {
        flag(node, arg.text, dynamicImport ? 'dynamically imports' : 'requires')
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return out
}

// ── Rule 2: option rules ────────────────────────────────────────────────────
export function optionViolations(rel, source) {
  const sf = parse(rel, source)
  const engine = inEngineDir(rel)
  const lines = source.split('\n')
  const out = []
  const seen = new Set()
  const flag = (node, what) => {
    const line = lineOf(sf, node)
    const key = `${line}:${what}`
    if (seen.has(key)) return
    seen.add(key)
    out.push(
      `${rel}:${line} ${what} — an ECharts formatter must be \`formatter: ${HELPER_NAME}(…)\` ` +
        `(components/charts/tooltip.ts), never a function, template or value the guard cannot see`,
    )
  }
  const allowed = (node, rule) => {
    const line = lineOf(sf, node)
    const re = ALLOW(rule)
    return re.test(lines[line - 1] ?? '') || re.test(lines[line - 2] ?? '')
  }

  // Local names bound to the approved helper by an import from the tooltip module.
  const helpers = new Set()
  const constObjects = new Set()
  for (const stmt of sf.statements) {
    if (ts.isImportDeclaration(stmt) && isStringish(stmt.moduleSpecifier) && HELPER_MODULE.test(stmt.moduleSpecifier.text)) {
      const nb = stmt.importClause?.namedBindings
      if (!stmt.importClause?.isTypeOnly && nb && ts.isNamedImports(nb)) {
        for (const el of nb.elements) {
          if (!el.isTypeOnly && (el.propertyName ?? el.name).text === HELPER_NAME) helpers.add(el.name.text)
        }
      }
    }
  }
  const collectConsts = (node) => {
    if (ts.isVariableDeclarationList(node) && node.flags & ts.NodeFlags.Const) {
      for (const d of node.declarations) {
        const init = unwrap(d.initializer)
        if (ts.isIdentifier(d.name) && init && ts.isObjectLiteralExpression(init)) constObjects.add(d.name.text)
      }
    }
    ts.forEachChild(node, collectConsts)
  }
  collectConsts(sf)

  const isHelperCall = (expr) => {
    const e = unwrap(expr)
    if (!e || !ts.isCallExpression(e)) return false
    const callee = unwrap(e.expression)
    return ts.isIdentifier(callee) && helpers.has(callee.text)
  }
  const isVisibleTooltipValue = (expr) => {
    const e = unwrap(expr)
    if (!e) return true
    if (ts.isObjectLiteralExpression(e)) return true
    if (ts.isArrayLiteralExpression(e)) return e.elements.every((el) => ts.isObjectLiteralExpression(unwrap(el)))
    if (e.kind === ts.SyntaxKind.TrueKeyword || e.kind === ts.SyntaxKind.FalseKeyword || e.kind === ts.SyntaxKind.NullKeyword) return true
    if (ts.isIdentifier(e)) return e.text === 'undefined' || constObjects.has(e.text)
    return false
  }
  // The name an object literal is stored under: `tooltip: {…}` or `const tooltip = {…}`.
  const ownerName = (obj) => {
    let p = obj.parent
    while (p && (ts.isParenthesizedExpression(p) || ts.isAsExpression(p) || ts.isSatisfiesExpression(p))) p = p.parent
    if (p && ts.isPropertyAssignment(p)) return keyOf(p.name)?.text ?? null
    if (p && ts.isVariableDeclaration(p) && ts.isIdentifier(p.name)) return p.name.text
    if (p && ts.isBinaryExpression(p) && p.right === obj) return p.left.getText(sf)
    return null
  }
  const checkKey = (node, name, what) => {
    const key = keyOf(name)
    if (!key) return null
    if (key.dynamic) {
      if (engine && !allowed(node, 'computed-key')) flag(node, `${what} has a computed key \`${name.getText(sf)}\` the guard cannot resolve`)
      return null
    }
    if (key.text === 'rich' && !allowed(node, 'rich')) {
      flag(node, '`rich` text templates interpret `{a|…}` markup — add `// chart-guard-allow rich: <reason>` if this is intended')
    }
    return key.text
  }

  const visit = (node) => {
    if (ts.isPropertyAssignment(node)) {
      const key = checkKey(node, node.name, 'a property')
      if (key && FORMATTER_KEY.test(key) && !isHelperCall(node.initializer)) flag(node, `sets \`${key}\` to something other than a ${HELPER_NAME}(…) call`)
      if (key && TOOLTIP_KEY.test(key) && !isVisibleTooltipValue(node.initializer)) flag(node, `sets \`${key}\` to \`${node.initializer.getText(sf)}\`, a value built elsewhere`)
    } else if (ts.isShorthandPropertyAssignment(node)) {
      const key = node.name.text
      if (key === 'rich' && !allowed(node, 'rich')) flag(node, '`rich` text templates interpret `{a|…}` markup')
      if (FORMATTER_KEY.test(key)) flag(node, `passes \`${key}\` as a shorthand property`)
      if (TOOLTIP_KEY.test(key) && !constObjects.has(key)) flag(node, `passes \`${key}\` as a shorthand property built elsewhere`)
    } else if (
      ts.isMethodDeclaration(node) || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node) ||
      ts.isPropertyDeclaration(node)
    ) {
      const key = checkKey(node, node.name, 'a member')
      if (key && FORMATTER_KEY.test(key) && !(ts.isPropertyDeclaration(node) && isHelperCall(node.initializer))) {
        flag(node, `defines \`${key}\` as a ${ts.isPropertyDeclaration(node) ? 'class field' : 'method or accessor'}`)
      }
    } else if (ts.isSpreadAssignment(node)) {
      const owner = ownerName(node.parent)
      if (owner && SINK_OWNER.test(owner) && !isHelperCall(node.expression)) {
        flag(node, `spreads \`${node.expression.getText(sf)}\` into \`${owner}\` — its keys are invisible to the guard`)
      }
    } else if (
      ts.isBinaryExpression(node) &&
      node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment
    ) {
      const left = unwrap(node.left)
      if (ts.isPropertyAccessExpression(left) || ts.isElementAccessExpression(left)) {
        const keyNode = ts.isPropertyAccessExpression(left) ? left.name : unwrap(left.argumentExpression)
        // `a.b` names b; `a['b']` names b; `a[b]` names nothing the guard can know.
        const literal = ts.isPropertyAccessExpression(left) || isStringish(keyNode)
        const key = literal ? keyNode.text : null
        if (key !== null) {
          if (FORMATTER_KEY.test(key) && !isHelperCall(node.right)) flag(node, `assigns \`${key}\``)
          if (key === 'rich' && !allowed(node, 'rich')) flag(node, 'assigns `rich`')
          if (TOOLTIP_KEY.test(key) && !isVisibleTooltipValue(node.right)) flag(node, `assigns \`${key}\` a value built elsewhere`)
        } else if (SINK_OWNER_ELEMENT.test(left.expression.getText(sf))) {
          flag(node, `assigns a computed key into \`${left.expression.getText(sf)}\``)
        }
      }
    } else if (ts.isCallExpression(node)) {
      const callee = unwrap(node.expression).getText(sf).replace(/\s/g, '')
      if (/^(?:Object\.(?:assign|defineProperty|defineProperties)|Reflect\.(?:set|defineProperty))$/.test(callee)) {
        const target = node.arguments[0]
        if (target && SINK_OWNER.test(target.getText(sf))) flag(node, `${callee}() writes into \`${target.getText(sf)}\``)
      }
    } else if (isStringish(node) && (FORMATTER_KEY.test(node.text) || node.text === 'rich')) {
      // Keys already judged above (property names, `x['formatter'] =` targets) and
      // module specifiers are skipped; any OTHER string that is a formatter key is a
      // key being built indirectly: defineProperty(tip, 'formatter', …), fromEntries, Reflect.set.
      const p = node.parent
      const judged =
        (ts.isComputedPropertyName(p)) ||
        ((ts.isPropertyAssignment(p) || ts.isMethodDeclaration(p) || ts.isPropertyDeclaration(p) ||
          ts.isGetAccessorDeclaration(p) || ts.isSetAccessorDeclaration(p)) && p.name === node) ||
        (ts.isElementAccessExpression(p) && ts.isBinaryExpression(p.parent) && p.parent.left === p) ||
        ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isLiteralTypeNode(p)
      if (!judged && !(node.text === 'rich' && allowed(node, 'rich'))) flag(node, `names the key '${node.text}' in a string`)
    } else if (
      (ts.isVariableDeclaration(node) || ts.isFunctionDeclaration(node) || ts.isParameter(node) || ts.isClassDeclaration(node)) &&
      node.name && ts.isIdentifier(node.name) && node.name.text === HELPER_NAME && rel !== HELPER_FILE
    ) {
      flag(node, `declares a local \`${HELPER_NAME}\` that shadows the approved helper`)
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return out
}

// ── Self-test ───────────────────────────────────────────────────────────────
const HELPER_IMPORT = `import { ${HELPER_NAME} } from '../../tooltip'\n`
const E = 'src/components/charts/engines/echarts/planted.ts' // inside the boundary
const OUT = 'src/pages/Planted.tsx' // outside it

/** Planted option code: every one must be caught. */
export const PLANTED_OPTION_BYPASSES = {
  // The 2026-09-22 review's eight bypasses …
  'computed literal key': "const o = { tooltip: { ['formatter']: (p: any) => '<b>' + p.name } }",
  'bracket assignment': "declare const option: any\noption.tooltip['formatter'] = (p: any) => p.name",
  'spread into tooltip': 'declare const sharedTooltip: object\nconst o = { tooltip: { ...sharedTooltip } }',
  'Object.assign into tooltip': 'declare const option: any, overrides: object\nObject.assign(option.tooltip, overrides)',
  'Object.defineProperty': "declare const tip: object, fn: unknown\nObject.defineProperty(tip, 'formatter', { value: fn })",
  'async method': 'const t = { async formatter(p: any) { return p.name } }',
  'method with multi-line args': 'const t = {\n  formatter(\n    params: any,\n  ) {\n    return params.name\n  },\n}',
  'helper || fallback': HELPER_IMPORT + 'declare const r: any\nconst o = { tooltip: { formatter: ' + HELPER_NAME + '(r) || ((p: any) => p.name) } }',
  // … the review's two controls …
  'aliased key (element access)': "declare const fn: unknown\nconst K = 'format' + 'ter'\nconst tooltip: any = {}\ntooltip[K] = fn",
  'typed formatter value': 'declare const trigger: string, fmt: unknown\nconst tooltip = { trigger, formatter: fmt as never }',
  // … rich text, and the predecessor's own cases.
  'rich label': "const o = { label: { rich: { a: { fontWeight: 'bold' } }, show: true } }",
  'string-returning arrow': "const o = { tooltip: { formatter: (p: any) => '<b>' + p.name + '</b>' } }",
  'template formatter': "const o = { tooltip: { formatter: '{b}: {c}' } }",
  'quoted key': "declare const render: unknown\nconst o = { 'formatter': render }",
  'valueFormatter': 'const o = { tooltip: { valueFormatter: (v: number) => `${v}` } }',
  'dot assignment': 'declare const option: any, render: unknown\noption.tooltip.formatter = render',
  'shorthand': 'declare const trigger: string, formatter: unknown\nconst tooltip = { trigger, formatter }',
  'computed dynamic key': "declare const K: string, fn: unknown\nconst o = { tooltip: { [K]: fn } }",
  'tooltip built elsewhere': "import { sharedTooltip } from './shared'\nconst o = { tooltip: sharedTooltip }",
  'fromEntries': "declare const f: unknown\nconst tip = Object.fromEntries([['formatter', f]])",
  'getter': "const t = { get formatter() { return (p: any) => p.name } }",
  'helper not imported from tooltip': `function ${HELPER_NAME}(f: unknown) { return f }\nconst o = { tooltip: { formatter: ${HELPER_NAME}(() => 'x') } }`,
  'rich assignment': "declare const label: any\nlabel['rich'] = {}",
}

/** Clean option code: none may be flagged. */
export const CLEAN_OPTIONS = {
  'helper formatter': HELPER_IMPORT + `const o = {\n  tooltip: {\n    formatter: ${HELPER_NAME}((params: unknown) => ({ rows: [] })),\n  },\n}`,
  'aliased helper': `import { ${HELPER_NAME} as dom } from '../../tooltip'\nconst o = { tooltip: { formatter: dom(() => ({ rows: [] })) } }`,
  'helper import only': `import { buildTooltipNode, ${HELPER_NAME} } from '../../tooltip'`,
  'Recharts JSX formatter (React escapes it)': 'const el = <Tooltip formatter={(val: number, name: string) => [val, name]} />',
  'rich with a reason': "const o = {\n  // chart-guard-allow rich: static bold prefix, no data interpolated\n  label: { rich: { b: { fontWeight: 'bold' } } },\n}",
  'label as a tooltip row': "declare const d: string[]\nconst row = { label: d[0] ?? '', value: 'x' }",
  'tooltip from a same-file const': "const shared = { trigger: 'item' }\nconst o = { tooltip: shared }",
  'shorthand axisLabel const': "const axisLabel = { color: 'var(--chart-axis)' }\nconst o = { xAxis: { axisLabel } }",
  'tooltip: false': 'const o = { tooltip: false }',
}

/** [label, rel, source, expect 'hit' | 'clean'] */
export const IMPORT_CASES = [
  ['value import of echarts outside the boundary', OUT, "import * as echarts from 'echarts/core'", 'hit'],
  ['partial zrender import outside', OUT, "import { merge } from 'zrender/lib/core/util'", 'hit'],
  ['side-effect import outside', OUT, "import 'echarts/charts'", 'hit'],
  ['dynamic import outside', OUT, "const m = () => import('three')", 'hit'],
  ['re-export outside', OUT, "export { use } from 'echarts/core'", 'hit'],
  ['require outside', OUT, "const gl = require('echarts-gl')", 'hit'],
  ['inline-type-only import outside', OUT, "import { type EChartsType } from 'echarts/core'", 'hit'],
  ['echarts barrel outside', OUT, "import { init } from 'echarts'", 'hit'],
  ['import type outside', OUT, "import type { ComposeOption } from 'echarts/core'", 'clean'],
  ['export type outside', OUT, "export type { EChartsType } from 'echarts'", 'clean'],
  ['typeof import() outside', OUT, "type E = typeof import('echarts/core')", 'clean'],
  ['unrelated package with the prefix', OUT, "import x from 'echartsy'\nimport y from 'three-ish'", 'clean'],
  ['value import inside the boundary', E, "import * as echarts from 'echarts/core'\nimport { HeatmapChart } from 'echarts/charts'", 'clean'],
]

export function runSelfTest() {
  const problems = []
  let count = 0
  for (const [label, src] of Object.entries(PLANTED_OPTION_BYPASSES)) {
    count += 1
    if (optionViolations(E, src).length === 0) problems.push(`self-test: the planted "${label}" bypass was NOT caught`)
  }
  for (const [label, src] of Object.entries(CLEAN_OPTIONS)) {
    count += 1
    const rel = label.includes('JSX') ? E.replace(/\.ts$/, '.tsx') : E
    const found = optionViolations(rel, src)
    if (found.length) problems.push(`self-test: the clean "${label}" was flagged: ${found.join('; ')}`)
  }
  for (const [label, rel, src, expect] of IMPORT_CASES) {
    count += 1
    const found = importBoundaryViolations(rel, src)
    if (expect === 'hit' && found.length === 0) problems.push(`self-test: the planted "${label}" was NOT caught`)
    if (expect === 'clean' && found.length) problems.push(`self-test: the clean "${label}" was flagged: ${found.join('; ')}`)
  }
  return { problems, count }
}
