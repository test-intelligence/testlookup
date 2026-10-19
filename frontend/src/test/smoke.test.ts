/**
 * Smoke tests — verify core utilities work correctly.
 * More component tests are added incrementally as features stabilise.
 */
import { describe, it, expect } from 'vitest'
import {
  formatDuration,
  formatPassRate,
  statusColor,
  confidenceColor,
} from '@/utils/formatters'

describe('formatters', () => {
  describe('formatDuration', () => {
    it('returns em-dash for null/undefined', () => {
      expect(formatDuration(null)).toBe('—')
      expect(formatDuration(undefined)).toBe('—')
    })
    it('formats milliseconds under 1 second', () => {
      expect(formatDuration(750)).toBe('750ms')
    })
    it('formats seconds for 1000–59999ms', () => {
      expect(formatDuration(3200)).toBe('3.2s')
    })
    it('formats minutes correctly', () => {
      expect(formatDuration(90000)).toBe('1m 30s')
    })
  })

  describe('formatPassRate', () => {
    it('returns em-dash for null', () => {
      expect(formatPassRate(null)).toBe('—')
    })
    it('formats percentage with one decimal', () => {
      expect(formatPassRate(92.3456)).toBe('92.3%')
      expect(formatPassRate(100)).toBe('100.0%')
    })
  })

  describe('statusColor', () => {
    it('returns correct colour for each status', () => {
      expect(statusColor('PASSED')).toBe('text-[var(--status-passed)]')
      expect(statusColor('FAILED')).toBe('text-[var(--status-failed)]')
      expect(statusColor('SKIPPED')).toBe('text-[var(--status-skipped)]')
      expect(statusColor('BROKEN')).toBe('text-[var(--status-broken)]')
    })
    it('returns muted colour for unknown status', () => {
      expect(statusColor('UNKNOWN')).toBe('text-[var(--color-text-secondary)]')
      expect(statusColor('')).toBe('text-[var(--color-text-secondary)]')
    })
    it('is case-insensitive', () => {
      expect(statusColor('passed')).toBe('text-[var(--status-passed)]')
      expect(statusColor('failed')).toBe('text-[var(--status-failed)]')
    })
  })

  // (W29a UIX-02: `categoryColor` is gone; the failure-category map is covered
  // by components/charts/failureGroups/categoryPatterns.test.tsx and
  // components/charts/taxonomyConsistency.test.ts.)

  // W29a UIX-07: a confidence score's colour is a MEANING (good / doubtful /
  // poor), so it reads the --ui-* aliases (the same pixels as the status hues).
  describe('confidenceColor', () => {
    it('returns the success colour for high confidence (>=80)', () => {
      expect(confidenceColor(80)).toBe('text-[var(--ui-success)]')
      expect(confidenceColor(95)).toBe('text-[var(--ui-success)]')
      expect(confidenceColor(100)).toBe('text-[var(--ui-success)]')
    })
    it('returns the warning colour for medium confidence (60-79)', () => {
      expect(confidenceColor(60)).toBe('text-[var(--ui-warning)]')
      expect(confidenceColor(75)).toBe('text-[var(--ui-warning)]')
      expect(confidenceColor(79)).toBe('text-[var(--ui-warning)]')
    })
    it('returns the danger colour for low confidence (<60)', () => {
      expect(confidenceColor(0)).toBe('text-[var(--ui-danger)]')
      expect(confidenceColor(59)).toBe('text-[var(--ui-danger)]')
    })
  })
})
