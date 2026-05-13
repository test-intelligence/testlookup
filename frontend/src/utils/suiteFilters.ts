export interface SuiteRunLike {
  primary_suite_name?: string | null
  suite_names?: string[] | null
}

export function normalizeSuiteName(value?: string | null): string {
  return (value ?? '').trim().toLowerCase()
}

export function suiteMatchesValue(value: string | null | undefined, suiteName: string): boolean {
  return normalizeSuiteName(value) === normalizeSuiteName(suiteName)
}

export function runHasSuite(run: SuiteRunLike, suiteName: string): boolean {
  const target = normalizeSuiteName(suiteName)
  if (!target) return true
  return [run.primary_suite_name, ...(run.suite_names ?? [])]
    .some(name => normalizeSuiteName(name) === target)
}

export function collectSuiteOptionsFromRuns(runs: SuiteRunLike[]): string[] {
  const byKey = new Map<string, string>()
  for (const run of runs) {
    for (const name of [run.primary_suite_name, ...(run.suite_names ?? [])]) {
      const key = normalizeSuiteName(name)
      if (key && !byKey.has(key)) byKey.set(key, (name ?? '').trim())
    }
  }
  return Array.from(byKey.values()).sort((a, b) => a.localeCompare(b))
}

export function collectSuiteOptions(names: Array<string | null | undefined>): string[] {
  const byKey = new Map<string, string>()
  for (const name of names) {
    const key = normalizeSuiteName(name)
    if (key && !byKey.has(key)) byKey.set(key, (name ?? '').trim())
  }
  return Array.from(byKey.values()).sort((a, b) => a.localeCompare(b))
}
