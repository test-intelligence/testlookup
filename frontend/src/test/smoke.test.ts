/**
 * Smoke tests — verify core utilities work correctly.
 * More component tests are added incrementally as features stabilise.
 */
import { describe, it, expect } from 'vitest'
import {
  formatDuration,
  formatPassRate,
  statusColor,
  categoryColor,
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
      expect(statusColor('PASSED')).toBe('text-emerald-400')
      expect(statusColor('FAILED')).toBe('text-red-400')
      expect(statusColor('SKIPPED')).toBe('text-amber-400')
      expect(statusColor('BROKEN')).toBe('text-orange-400')
    })
    it('returns muted colour for unknown status', () => {
      expect(statusColor('UNKNOWN')).toBe('text-neutral-400')
      expect(statusColor('')).toBe('text-neutral-400')
    })
    it('is case-insensitive', () => {
      expect(statusColor('passed')).toBe('text-emerald-400')
      expect(statusColor('failed')).toBe('text-red-400')
    })
  })

  describe('categoryColor', () => {
    it('maps each failure category to the right colour', () => {
      expect(categoryColor('PRODUCT_BUG')).toBe('text-red-400')
      expect(categoryColor('INFRASTRUCTURE')).toBe('text-orange-400')
      expect(categoryColor('TEST_DATA')).toBe('text-amber-400')
      expect(categoryColor('AUTOMATION_DEFECT')).toBe('text-purple-400')
      expect(categoryColor('FLAKY')).toBe('text-pink-400')
    })
    it('returns slate for unknown category', () => {
      expect(categoryColor('UNKNOWN')).toBe('text-neutral-400')
    })
  })

  describe('confidenceColor', () => {
    it('returns green for high confidence (>=80)', () => {
      expect(confidenceColor(80)).toBe('text-emerald-400')
      expect(confidenceColor(95)).toBe('text-emerald-400')
      expect(confidenceColor(100)).toBe('text-emerald-400')
    })
    it('returns amber for medium confidence (60-79)', () => {
      expect(confidenceColor(60)).toBe('text-amber-400')
      expect(confidenceColor(75)).toBe('text-amber-400')
      expect(confidenceColor(79)).toBe('text-amber-400')
    })
    it('returns red for low confidence (<60)', () => {
      expect(confidenceColor(0)).toBe('text-red-400')
      expect(confidenceColor(59)).toBe('text-red-400')
    })
  })
})
