/**
 * HON-03: every Go / JavaScript identifier the /live "Connect a test runner"
 * panel shows must exist in the SDK that panel downloads.
 *
 * The SDK surface comes from sdkSymbols.json, generated from client/go and
 * client/js by scripts/gen_sdk_symbols.py (the frontend image builds from
 * frontend/ alone, so this test cannot read client/ itself). The quality gate
 * `frontend.sdk-symbols-fresh` keeps that file in step with the SDK sources.
 *
 * Before this test the JS and Go snippets called RunWithReporter,
 * NewReporter, CreateSession, WithRelease, WithError and createSession, and
 * set TESTLOOKUP_PROJECT, none of which the SDKs export or read.
 */
import { describe, expect, it } from 'vitest'

import symbols from './sdkSymbols.json'
import {
  JS_SDK_TARBALL,
  SDK_TABS,
  getAPISnippet,
  getInstallSnippet,
  getRunnerSnippet,
  type SDKLang,
} from './sdkSnippets'

const PROJECT_ID = '3f2b9c1e-0000-4000-8000-000000000042'
const SERVER_URL = 'http://localhost:8000'

function allSnippets(lang: SDKLang): string {
  return [
    getInstallSnippet(lang),
    getRunnerSnippet(lang, PROJECT_ID, SERVER_URL),
    getAPISnippet(lang, PROJECT_ID, SERVER_URL),
  ].join('\n')
}

/** Keys of an object / struct literal body: `name:` at a line or after `{` / `,`. */
function literalKeys(body: string): string[] {
  return [...body.matchAll(/(?:^|[{,\s])(\w+)\??:\s/g)].map((m) => m[1])
}

function unknown(used: Iterable<string>, known: readonly string[]): string[] {
  const allowed = new Set(known)
  return [...new Set(used)].filter((name) => !allowed.has(name)).sort()
}

describe('sdkSnippets — Go identifiers exist in client/go', () => {
  const go = symbols.go
  const code = allSnippets('go')
  const packageLevel = [...go.functions, ...go.types, ...go.constants]
  const methods = go.methods as Record<string, string[]>
  const fields = go.fields as Record<string, string[]>

  it('every testlookup.X is an exported function, type or constant', () => {
    const used = [...code.matchAll(/(?<![\w/.-])testlookup\.([A-Za-z_]\w*)/g)].map((m) => m[1])
    expect(used.length).toBeGreaterThan(5)
    expect(unknown(used, packageLevel)).toEqual([])
  })

  it('every method call on a reporter, session or test reporter exists on that type', () => {
    const receivers: Record<string, string> = {
      reporter: 'Reporter',
      session: 'Session',
      tr: 'TestReporter',
    }
    // Anything else before a `.Name` must be the SDK package or the stdlib.
    const stdlib = new Set(['testlookup', 'os', 'context', 'log', 'time', 'testing', 'm', 't'])
    const calls = [...code.matchAll(/\b([a-z]\w*)\.([A-Z]\w*)/g)]
    expect(calls.length).toBeGreaterThan(5)
    for (const [, receiver, name] of calls) {
      const type = receivers[receiver]
      if (type) {
        expect(methods[type] ?? [], `${receiver}.${name} — ${type} has no such method`).toContain(name)
      } else {
        expect(stdlib.has(receiver), `unexpected receiver ${receiver}.${name}`).toBe(true)
      }
    }
  })

  it('every struct literal field exists on that struct', () => {
    const literals = [...code.matchAll(/testlookup\.(\w+)\{([^}]*)\}/g)]
    expect(literals.length).toBeGreaterThan(2)
    for (const [, type, body] of literals) {
      expect(fields[type], `testlookup.${type} is not a struct`).toBeDefined()
      expect(unknown(literalKeys(body), fields[type]), `testlookup.${type}{…}`).toEqual([])
    }
  })

  it('names only the environment variables New / NewFromEnv read', () => {
    const used = [...code.matchAll(/\bTESTLOOKUP_[A-Z0-9_]+/g)].map((m) => m[0])
    expect(used).toContain('TESTLOOKUP_PROJECT_ID')
    expect(unknown(used, go.env)).toEqual([])
  })

  it('uses the module path and import path go.mod declares', () => {
    const paths = [...code.matchAll(/github\.com\/[\w./-]+/g)].map((m) => m[0].replace(/@.*$/, ''))
    expect(paths.length).toBeGreaterThan(2)
    expect(unknown(paths, [go.module, go.import_path])).toEqual([])
  })

  it('installs from the downloaded ZIP, not from a registry', () => {
    const install = getInstallSnippet('go')
    expect(install).toContain('Download the Go ZIP')
    expect(install).toMatch(/-replace=github\.com\/testlookup\/testlookup-go=\.\/go/)
    expect(code).not.toMatch(/\bgo get\b/)
  })
})

describe('sdkSnippets — JavaScript identifiers exist in client/js', () => {
  const js = symbols.js
  const code = allSnippets('javascript')
  const members = js.members as Record<string, string[]>
  const fields = js.fields as Record<string, string[]>

  it('imports only what the package exports', () => {
    const imports = [...code.matchAll(/import\s*\{([^}]+)\}\s*from\s*'([^']+)'/g)]
    expect(imports.length).toBe(1)
    for (const [, names, from] of imports) {
      expect(from).toBe(js.package)
      const used = names.split(',').map((n) => n.trim()).filter(Boolean)
      expect(unknown(used, js.exports)).toEqual([])
    }
  })

  it('loads only the reporter entry points package.json exports', () => {
    const used = [...code.matchAll(/testlookup-reporter\/([\w-]+)/g)].map((m) => m[1])
    expect(used).toEqual(expect.arrayContaining(['jest-reporter', 'mocha-reporter']))
    expect(unknown(used, js.subpaths)).toEqual([])
  })

  it('calls only public members of TestLookupReporter and LiveSession', () => {
    const onReporter = [...code.matchAll(/\breporter\.(\w+)/g)].map((m) => m[1])
    const onSession = [...code.matchAll(/\bsession\.(\w+)/g)].map((m) => m[1])
    expect(onReporter).toContain('startSession')
    expect(unknown(onReporter, members.TestLookupReporter)).toEqual([])
    expect(unknown(onSession, members.LiveSession)).toEqual([])
  })

  it('passes only fields the config, session and record options declare', () => {
    const ctor = code.match(/new TestLookupReporter\(\{([^}]*)\}\)/)
    expect(ctor).not.toBeNull()
    expect(unknown(literalKeys(ctor?.[1] ?? ''), fields.ReporterConfig)).toEqual([])

    const start = code.match(/startSession\(\{([^}]*)\}/)
    expect(start).not.toBeNull()
    expect(unknown(literalKeys(start?.[1] ?? ''), fields.SessionOptions)).toEqual([])

    for (const [, body] of code.matchAll(/session\.record\([^{)]*\{([^}]*)\}/g)) {
      expect(unknown(literalKeys(body), fields.RecordOptions)).toEqual([])
    }
  })

  it('passes only options the Jest and Mocha reporters read', () => {
    const jest = code.match(/\['testlookup-reporter\/jest-reporter',\s*\{([^}]*)\}/)
    expect(jest).not.toBeNull()
    expect(unknown(literalKeys(jest?.[1] ?? ''), js.jest_options)).toEqual([])

    const mocha = [...code.matchAll(/--reporter-option\s+(\w+)=/g)].map((m) => m[1])
    expect(mocha.length).toBeGreaterThan(0)
    expect(unknown(mocha, js.mocha_options)).toEqual([])
  })

  it('names only environment variables the reporters read', () => {
    const used = [...code.matchAll(/\bTESTLOOKUP_[A-Z0-9_]+/g)].map((m) => m[0])
    expect(unknown(used, js.env)).toEqual([])
  })

  it('installs the tarball npm pack writes for this package version', () => {
    expect(JS_SDK_TARBALL).toBe(`${js.package}-${js.version}.tgz`)
    const install = getInstallSnippet('javascript')
    expect(install).toContain('Download the JavaScript/TS ZIP')
    expect(install).toContain(`npm install --save-dev ./js/${JS_SDK_TARBALL}`)
    expect(install).not.toMatch(/npm install testlookup-reporter\b/)
  })
})

describe('sdkSnippets — every tab renders for the active project', () => {
  it.each(SDK_TABS.map((t) => t.id))('%s runner and API snippets carry the project and server', (lang) => {
    const code = getRunnerSnippet(lang, PROJECT_ID, SERVER_URL) + getAPISnippet(lang, PROJECT_ID, SERVER_URL)
    expect(code).toContain(SERVER_URL)
    expect(code).toContain(PROJECT_ID)
  })
})

describe('sdkSnippets — W18 DEV-03 runner snippets', () => {
  const js = symbols.js
  const ADAPTER_OPTIONS = ['testlookupUrl', 'testlookupProject', 'testlookupApiKey']

  it.each([
    ['playwright', 'playwright-reporter'],
    ['vitest', 'vitest-reporter'],
    ['cypress', 'cypress-plugin'],
    ['cucumber', 'cucumber-formatter'],
  ])('%s loads an entry point package.json exports, with adapter option names', (runner, subpath) => {
    const code = getRunnerSnippet('javascript', PROJECT_ID, SERVER_URL, runner)
    expect(code).toContain(`testlookup-reporter/${subpath}`)
    expect(js.subpaths).toContain(subpath)
    const keys = [...code.matchAll(/\b(testlookup[A-Z]\w*)\b/g)].map((m) => m[1])
    expect(keys.length).toBeGreaterThan(0)
    expect(keys.filter((k) => !ADAPTER_OPTIONS.includes(k))).toEqual([])
  })

  it('Robot names the listener class the Python SDK ships', () => {
    const code = getRunnerSnippet('python', PROJECT_ID, SERVER_URL, 'robot')
    expect(code).toContain('testlookup_robot_listener.TestLookupListener')
  })

  it('the default runner keeps the existing snippet', () => {
    expect(getRunnerSnippet('javascript', PROJECT_ID, SERVER_URL, 'jest-mocha')).toBe(getRunnerSnippet('javascript', PROJECT_ID, SERVER_URL))
    expect(getRunnerSnippet('python', PROJECT_ID, SERVER_URL, 'pytest')).toBe(getRunnerSnippet('python', PROJECT_ID, SERVER_URL))
  })
})
