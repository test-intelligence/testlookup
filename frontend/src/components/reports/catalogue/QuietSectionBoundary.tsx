/**
 * The boundary around a page's Wave 3 additions (the Coverage, Failures and
 * Suite detail composites): if their chunk cannot load or a section throws,
 * it renders NOTHING in their place, so the page around them stays whole —
 * the sections are additions, the page is not.
 *
 * Nothing on screen, but never silent (R1B-9): the failure is logged to the
 * console under the composite's name and sent to the error telemetry every
 * other boundary uses (`reportBoundaryError`), so a render bug that removes a
 * whole block of charts is still found.
 *
 * Statically in each page's chunk (the composites are): it imports nothing
 * the page did not already have (`errorReporting` is in the app shell).
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { reportBoundaryError } from '@/utils/errorReporting'

export interface QuietSectionBoundaryProps {
  /** Which page's additions these are ("coverage", "failures", "suite-detail"), for the log. */
  label: string
  children: ReactNode
}

export class QuietSectionBoundary extends Component<QuietSectionBoundaryProps, { failed: boolean }> {
  /** The console line a hidden composite leaves, by its label. */
  static message(label: string): string {
    return `[catalogue] the ${label} charts failed and were hidden; the page is unaffected.`
  }

  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Telemetry must never break the page it is reporting on.
    try {
      console.error(QuietSectionBoundary.message(this.props.label), error)
      reportBoundaryError(error, info.componentStack ?? '')
    } catch {
      // nothing: the block is already hidden
    }
  }

  render() {
    return this.state.failed ? null : this.props.children
  }
}

export default QuietSectionBoundary
