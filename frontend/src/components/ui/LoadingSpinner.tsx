import { clsx } from 'clsx'

interface Props { size?: 'sm' | 'md' | 'lg'; className?: string }

export default function LoadingSpinner({ size = 'md', className }: Props) {
  return (
    <div className={clsx(
      // The moving arc is the accent (W29a UIX-01 L1): a white arc vanished on the light themes.
      'animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-accent)]',
      size === 'sm' && 'h-4 w-4',
      size === 'md' && 'h-8 w-8',
      size === 'lg' && 'h-12 w-12',
      className,
    )} />
  )
}
