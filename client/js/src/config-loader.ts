/**
 * TestLookup config loader for the JS / TS SDK.
 *
 * Reads a declarative ``testlookup.properties`` file (preferred — matches the
 * ReportPortal convention) or a legacy ``testlookup.yaml`` from the cwd /
 * ``.testlookup`` / home directory, then overlays environment variables.
 *
 * Discovery order (first match wins per file kind):
 *   1. ./testlookup.properties
 *   2. ./testlookup.yaml
 *   3. ./.testlookup/testlookup.properties
 *   4. ./.testlookup/config.yaml
 *   5. ~/.testlookup/testlookup.properties
 *   6. ~/.testlookup/config.yaml
 *
 * Canonical key prefix is ``testlookup.*`` — matches the dotted convention
 * used across all four SDKs.
 */

import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

export interface ResolvedConfig {
  baseUrl?: string
  apiKey?: string
  token?: string
  projectId?: string
  launchName?: string
  /**
   * Run-level suite identifier. Sourced from testlookup.suite (preferred) with
   * testlookup.launch as the documented fallback. Stamped on every record() the
   * SDK ships and sent on session create so the server populates
   * LiveSession.suite_name and TestRun.primary_suite_name.
   */
  suiteName?: string
  /**
   * Release this test run belongs to. Sourced from testlookup.release /
   * TESTLOOKUP_RELEASE. Sent on session create; when blank the server falls
   * back to the project's default release.
   */
  releaseName?: string
  buildNumber?: string
  branch?: string
  commitHash?: string
  framework?: string
}

const PROPERTIES_KEYS: Record<string, keyof ResolvedConfig> = {
  'testlookup.endpoint':  'baseUrl',
  'testlookup.url':       'baseUrl',
  'testlookup.api.key':   'apiKey',
  'testlookup.api_key':   'apiKey',
  'testlookup.apiKey':    'apiKey',
  'testlookup.token':     'token',
  'testlookup.project':   'projectId',
  'testlookup.launch':    'launchName',
  'testlookup.suite':     'suiteName',
  'testlookup.release':   'releaseName',
  'testlookup.build':     'buildNumber',
  'testlookup.branch':    'branch',
  'testlookup.commit':    'commitHash',
  'testlookup.framework': 'framework',
}

const ENV_KEYS: Record<string, keyof ResolvedConfig> = {
  TESTLOOKUP_URL:        'baseUrl',
  TESTLOOKUP_ENDPOINT:   'baseUrl',
  TESTLOOKUP_API_KEY:    'apiKey',
  TESTLOOKUP_TOKEN:      'token',
  TESTLOOKUP_PROJECT_ID: 'projectId',
  TESTLOOKUP_PROJECT:    'projectId',
  TESTLOOKUP_LAUNCH:     'launchName',
  TESTLOOKUP_SUITE:      'suiteName',
  TESTLOOKUP_RELEASE:    'releaseName',
  TESTLOOKUP_BUILD:      'buildNumber',
  TESTLOOKUP_BRANCH:     'branch',
  TESTLOOKUP_COMMIT:     'commitHash',
  TESTLOOKUP_FRAMEWORK:  'framework',
}

const YAML_PATHS: Array<{ section: string[]; key: string; field: keyof ResolvedConfig }> = [
  { section: ['server'],    key: 'url',          field: 'baseUrl' },
  { section: ['auth'],      key: 'api_key',      field: 'apiKey' },
  { section: ['auth'],      key: 'token',        field: 'token' },
  { section: ['project'],   key: 'id',           field: 'projectId' },
  { section: ['reporting'], key: 'launch_name',  field: 'launchName' },
  { section: ['reporting'], key: 'suite_name',   field: 'suiteName' },
  { section: ['reporting'], key: 'release_name', field: 'releaseName' },
  { section: ['ci'],        key: 'build_number', field: 'buildNumber' },
  { section: ['ci'],        key: 'branch',       field: 'branch' },
  { section: ['ci'],        key: 'commit_hash',  field: 'commitHash' },
  { section: ['reporting'], key: 'framework',    field: 'framework' },
]

function findFile(): { path: string; kind: 'properties' | 'yaml' } | null {
  const candidates: Array<{ rel: string; kind: 'properties' | 'yaml' }> = [
    { rel: 'testlookup.properties',                    kind: 'properties' },
    { rel: 'testlookup.yaml',                          kind: 'yaml' },
    { rel: path.join('.testlookup', 'testlookup.properties'), kind: 'properties' },
    { rel: path.join('.testlookup', 'config.yaml'),    kind: 'yaml' },
  ]
  for (const c of candidates) {
    if (fs.existsSync(c.rel)) return { path: c.rel, kind: c.kind }
  }
  const home = os.homedir()
  for (const name of ['testlookup.properties', 'config.yaml']) {
    const full = path.join(home, '.testlookup', name)
    if (fs.existsSync(full)) {
      return { path: full, kind: name.endsWith('.properties') ? 'properties' : 'yaml' }
    }
  }
  return null
}

function parseProperties(content: string): ResolvedConfig {
  const out: ResolvedConfig = {}
  for (const raw of content.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || line.startsWith('!')) continue
    const eq = line.indexOf('=')
    const colon = line.indexOf(':')
    let sep = -1
    if (eq >= 0 && (colon < 0 || eq < colon)) sep = eq
    else if (colon >= 0) sep = colon
    if (sep < 0) continue
    const key = line.slice(0, sep).trim()
    const val = line.slice(sep + 1).trim()
    const field = PROPERTIES_KEYS[key]
    if (field && val) (out as Record<string, string>)[field] = val
  }
  return out
}

function parseYaml(content: string): ResolvedConfig {
  // Lazy require so yaml isn't a hard dependency. The Java/Python SDKs treat
  // YAML the same way — present if installed, skipped otherwise.
  let parsed: unknown
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const yaml = require('yaml') as { parse: (s: string) => unknown }
    parsed = yaml.parse(content)
  } catch {
    return {}
  }
  if (!parsed || typeof parsed !== 'object') return {}
  const cfg = parsed as Record<string, unknown>
  const out: ResolvedConfig = {}
  for (const { section, key, field } of YAML_PATHS) {
    let cur: unknown = cfg
    for (const s of section) {
      if (cur && typeof cur === 'object' && s in (cur as Record<string, unknown>)) {
        cur = (cur as Record<string, unknown>)[s]
      } else {
        cur = undefined
        break
      }
    }
    if (cur && typeof cur === 'object') {
      const leaf = (cur as Record<string, unknown>)[key]
      if (typeof leaf === 'string' && leaf) (out as Record<string, string>)[field] = leaf
    }
  }
  return out
}

/**
 * Resolve TestLookup config: file → env var overlay. Caller-provided values
 * take final precedence; merge what's returned here with any explicit
 * constructor args.
 */
export function loadConfig(): ResolvedConfig {
  let cfg: ResolvedConfig = {}
  const found = findFile()
  if (found) {
    try {
      const content = fs.readFileSync(found.path, 'utf8')
      cfg = found.kind === 'properties' ? parseProperties(content) : parseYaml(content)
    } catch (err) {
      console.warn('[TestLookup] Failed to load config file', found.path, err)
    }
  }

  for (const [envName, field] of Object.entries(ENV_KEYS)) {
    const val = process.env[envName]
    if (val) (cfg as Record<string, string>)[field] = val
  }

  return cfg
}

/** Return true if any TestLookup config source is present. */
export function isConfigured(): boolean {
  if (findFile() != null) return true
  for (const k of Object.keys(ENV_KEYS)) {
    if (process.env[k]) return true
  }
  return false
}
