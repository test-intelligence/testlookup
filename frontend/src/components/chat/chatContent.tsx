/**
 * Ask-AI chat text: starter questions, the run page's question, source links
 * and the "how it was answered" line. Helpers only (no components), so the
 * component files keep fast refresh.
 */
import { GitCompare, Rocket, Shuffle, Sparkles, XCircle } from 'lucide-react'
import type { ChatMeta, ChatSource } from '@/types/chat'

/** "build 105", or "build-2029" as written. */
export function buildLabel(build: string | number | null | undefined, capital = false): string {
  const text = build === null || build === undefined ? '' : String(build)
  if (!text) return 'the latest run'
  if (/^build/i.test(text)) return text
  return `${capital ? 'Build' : 'build'} ${text}`
}

export interface StarterPrompt {
  icon: React.ReactNode
  label: string
  prompt: string
}

/** Starter questions, named after the project and its latest build. */
export function starterPrompts(projectName: string, latestBuild?: string | null): StarterPrompt[] {
  return [
    { icon: <XCircle className="w-4 h-4" />, label: 'What failed', prompt: `What failed in ${buildLabel(latestBuild)}, and why?` },
    { icon: <GitCompare className="w-4 h-4" />, label: 'What changed', prompt: 'What changed since the previous build? Any new failures?' },
    { icon: <Shuffle className="w-4 h-4" />, label: 'Flaky tests', prompt: 'Which tests are flaky right now, and are any of them quarantined?' },
    { icon: <Rocket className="w-4 h-4" />, label: 'Release', prompt: `Is ${projectName} ready to release? What blocks it?` },
    { icon: <Sparkles className="w-4 h-4" />, label: 'Standup summary', prompt: "Summarize this project's recent test health for a standup, in five bullets." },
  ]
}

/** The question "Ask AI about this run" writes for the reader. */
export function askAboutRun(build: string | number | null | undefined): string {
  const label = build === null || build === undefined || build === '' ? 'this run' : buildLabel(build)
  return `What failed in ${label}, and why? Are any of these failures new since the previous build?`
}

export interface SourceLink {
  key: string
  to: string
  label: string
  kind: 'run' | 'test'
}

/** The runs and tests an answer names, as links to their pages. */
export function sourceLinks(sources: ChatSource[]): SourceLink[] {
  const links: SourceLink[] = []
  const seen = new Set<string>()
  for (const s of sources) {
    let link: Omit<SourceLink, 'key'> | null = null
    if (s.type === 'test_run' && s.id && s.build !== undefined && s.build !== null && s.build !== '') {
      link = { to: `/runs/${s.id}`, label: buildLabel(s.build, true), kind: 'run' }
    } else if (s.type === 'test_case' && s.id && s.run_id && s.name) {
      link = { to: `/runs/${s.run_id}/tests/${s.id}`, label: s.name, kind: 'test' }
    }
    if (!link || seen.has(link.to)) continue
    seen.add(link.to)
    links.push({ key: link.to, ...link })
  }
  return links
}

function seconds(ms?: number | null): string | null {
  if (ms === null || ms === undefined) return null
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** "mistral-nemo · first words in 1.4 s · answered in 4.3 s · 2 lookups" */
export function metaLine(meta: ChatMeta | null): string | null {
  if (!meta) return null
  const parts: string[] = []
  if (meta.model) parts.push(meta.model.split('/').pop() ?? meta.model)
  const first = seconds(meta.first_token_ms)
  if (first) parts.push(`first words in ${first}`)
  const total = seconds(meta.total_ms)
  if (total) parts.push(`${meta.status === 'stopped' ? 'stopped after' : 'answered in'} ${total}`)
  // `lookups` counts the up-front lookups too; answers saved before it existed
  // carry only the model's own `tool_calls`.
  const lookups = meta.lookups ?? meta.tool_calls
  if (lookups) parts.push(`${lookups} lookup${lookups === 1 ? '' : 's'}`)
  return parts.length ? parts.join(' · ') : null
}
