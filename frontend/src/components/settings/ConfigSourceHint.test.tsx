/**
 * The "Set here" / "Server default" hint under a Settings › AI control (W04).
 *
 * `ai_config` is the ONLY source that reads "Set here": an `env_default`, an
 * unknown value, or a field an older backend does not send all read "Server
 * default" — never a claim that the page set something it did not.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import ConfigSourceHint from './ConfigSourceHint'

describe('ConfigSourceHint', () => {
  it('reads "Set here" for a stored value, with the note', () => {
    render(<ConfigSourceHint configKey="deep_investigation_enabled" source="ai_config" note="Applies within a minute." />)
    const hint = screen.getByText('Set here · Applies within a minute.')
    expect(hint.tagName).toBe('P')
    expect(hint).toHaveAttribute('data-config-source', '')
    expect(hint).toHaveAttribute('data-config-source-key', 'deep_investigation_enabled')
    expect(hint).toHaveAttribute('data-source', 'ai_config')
  })

  it('reads "Server default" for the env default, and no separator without a note', () => {
    render(<ConfigSourceHint configKey="analysis_mode" source="env_default" />)
    const hint = screen.getByText('Server default')
    expect(hint.textContent).toBe('Server default')
    expect(hint).toHaveAttribute('data-source', 'env_default')
  })

  it('never claims "Set here" for an unknown or missing source', () => {
    render(
      <>
        <ConfigSourceHint configKey="a" source="override" testId="unknown" />
        <ConfigSourceHint configKey="b" source="" testId="empty" />
        <ConfigSourceHint configKey="finetune_enabled" source={undefined} testId="ft" />
      </>,
    )
    expect(screen.getByTestId('unknown')).toHaveTextContent(/^Server default$/)
    expect(screen.getByTestId('empty')).toHaveTextContent(/^Server default$/)
    expect(screen.getByTestId('ft')).toHaveTextContent(/^Server default$/)
    expect(screen.getByTestId('ft')).toHaveAttribute('data-source', 'env_default')
  })
})
