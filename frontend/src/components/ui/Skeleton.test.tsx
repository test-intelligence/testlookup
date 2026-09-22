/**
 * VIZ-109: skeletons are hidden from assistive tech, reserve their space up
 * front, and only animate for users who have not asked for reduced motion.
 */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import Skeleton from './Skeleton'

const root = (container: HTMLElement) => container.firstElementChild as HTMLElement

describe('Skeleton', () => {
  it.each(['line', 'block', 'chart'] as const)('%s is aria-hidden and has no text', (variant) => {
    const { container } = render(<Skeleton variant={variant} />)
    expect(root(container)).toHaveAttribute('aria-hidden', 'true')
    expect(root(container)).toHaveAttribute('data-skeleton', variant)
    expect(container.textContent).toBe('')
  })

  it.each<[('line' | 'block' | 'chart'), string]>([
    ['block', '96px'],
    ['chart', '240px'],
  ])('%s reserves a default height of %s', (variant, height) => {
    const { container } = render(<Skeleton variant={variant} />)
    expect(root(container).style.height).toBe(height)
    expect(root(container).style.width).toBe('100%')
  })

  it('takes an explicit size so the loaded content lands in the same box', () => {
    const { container } = render(<Skeleton variant="chart" width={640} height="18rem" />)
    expect(root(container).style.width).toBe('640px')
    expect(root(container).style.height).toBe('18rem')
  })

  it('stacks lines, shortening the last', () => {
    const { container } = render(<Skeleton variant="line" lines={3} height={10} />)
    const bars = Array.from(root(container).children) as HTMLElement[]
    expect(bars).toHaveLength(3)
    expect(bars.map((bar) => bar.style.width)).toEqual(['100%', '100%', '60%'])
    expect(bars.every((bar) => bar.style.height === '10px')).toBe(true)
  })

  it('pulses only under motion-safe (prefers-reduced-motion respected)', () => {
    const { container } = render(<Skeleton variant="chart" />)
    const animated = container.querySelectorAll('[class*="animate-"]')
    expect(animated.length).toBeGreaterThan(0)
    animated.forEach((node) => {
      const classes = node.className.split(/\s+/).filter((c) => c.includes('animate-'))
      expect(classes.every((c) => c.startsWith('motion-safe:'))).toBe(true)
    })
  })
})
