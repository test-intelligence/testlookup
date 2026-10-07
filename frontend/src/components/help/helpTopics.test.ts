import { describe, expect, it } from 'vitest'
import { findDocPage } from '@/content/guide/manifest'
import { DOC_SOURCES } from '@/content/guide/sources'
import { anchorIds } from '@/content/guide/slug'
import { ADMIN_ITEM, NAV_ITEMS } from '@/components/layout/navConfig'
import { DEFAULT_HELP_TOPIC, HELP_TOPICS, helpTopicFor, helpTopicParam } from './helpTopics'

describe('helpTopics (UX redesign P1)', () => {
  it('every topic is a documentation page, and every anchor a heading in it', () => {
    const broken = [...Object.entries(HELP_TOPICS), ['(default)', DEFAULT_HELP_TOPIC] as const].flatMap(([route, t]) => {
      if (!findDocPage(t.topic)) return [`${route}: no page "${t.topic}"`]
      if (t.anchor && !anchorIds(DOC_SOURCES[t.topic] ?? '').includes(t.anchor)) return [`${route}: no "#${t.anchor}" in ${t.topic}`]
      return []
    })
    expect(broken).toEqual([])
  })

  it('every sidebar place, and every section tab, has its own topic (not the default)', () => {
    const paths = [...NAV_ITEMS, ADMIN_ITEM].flatMap((item) => [item.to, ...(item.tabs ?? []).map((t) => t.to)])
    expect(paths.filter((p) => helpTopicFor(p) === DEFAULT_HELP_TOPIC)).toEqual([])
  })

  it('the longest prefix wins, and an unknown page falls back to the introduction', () => {
    expect(helpTopicFor('/settings/sso')).toEqual({ topic: 'security' })
    expect(helpTopicFor('/settings/ai')).toEqual({ topic: 'administration' })
    expect(helpTopicFor('/defects')).toEqual({ topic: 'failure-analysis', anchor: 'promoting-to-a-defect' })
    expect(helpTopicFor('/runs/r1/intelligence')).toEqual({ topic: 'ingestion' })
    expect(helpTopicFor('/nowhere')).toBe(DEFAULT_HELP_TOPIC)
  })

  it("helpTopicParam keeps the route's section for PageHeader (topic#anchor)", () => {
    expect(helpTopicParam('/defects')).toBe('failure-analysis#promoting-to-a-defect')
    expect(helpTopicParam('/failures')).toBe('failure-analysis')
  })
})
