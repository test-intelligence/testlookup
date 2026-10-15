/**
 * W03 AIH-04: the AI history control. Its header names the version shown (`v2 · LLM llama3.1 · ...`), the version
 * select lists Current then every version newest first, the compare select offers every other version (and the
 * current data while a stored version is viewed); an empty history reads "First analysis of this run".
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AIVersionPicker, { type AIVersionPickerProps } from './AIVersionPicker'
import { versionSummary } from './aiHistory.fixtures'

const VERSIONS = [versionSummary(1, { generated_at: null }), versionSummary(2, { generated_at: null })]

function renderPicker(props: Partial<AIVersionPickerProps> = {}) {
  const onSelect = vi.fn()
  const onCompare = vi.fn()
  const view = render(
    <AIVersionPicker versions={VERSIONS} latestVersion={2} selected={null} onSelect={onSelect} compare={null} onCompare={onCompare} {...props} />,
  )
  return { ...view, onSelect, onCompare }
}

const options = (select: HTMLElement) => Array.from(select.querySelectorAll('option')).map((o) => [o.value, o.textContent])

describe('AIVersionPicker', () => {
  it('names the latest version as current and lists Current, then versions newest first', () => {
    const { container } = renderPicker()
    expect(container.querySelector('[data-ai-version-meta]')).toHaveTextContent('v2 · LLM llama3.1 (current)')
    const select = screen.getByRole('combobox', { name: 'AI analysis version' })
    expect(select).toHaveValue('')
    expect(options(select)).toEqual([['', 'Current'], ['2', 'v2 · LLM llama3.1'], ['1', 'v1 · rules']])
    // Viewing Current: every stored version can be compared with it; "Current data" would compare it with itself.
    expect(options(screen.getByRole('combobox', { name: 'Compare with' }))).toEqual([
      ['', 'Compare with…'], ['2', 'v2 · LLM llama3.1'], ['1', 'v1 · rules'],
    ])
    expect(container.querySelector('[data-ai-version-deterministic]')).toBeNull()
  })

  it('a stored version shown: its meta, a deterministic chip for a rules version, and "Current data" to compare with', () => {
    const { container } = renderPicker({ selected: 1 })
    expect(container.querySelector('[data-ai-version-meta]')).toHaveTextContent(/^v1 · rules$/)
    expect(container.querySelector('[data-ai-version-deterministic]')).toHaveTextContent('deterministic')
    expect(screen.getByRole('combobox', { name: 'AI analysis version' })).toHaveValue('1')
    expect(options(screen.getByRole('combobox', { name: 'Compare with' }))).toEqual([
      ['', 'Compare with…'], ['2', 'v2 · LLM llama3.1'], ['live', 'Current data'],
    ])
  })

  it('reports the choices: a version, Current, a version to compare, the current data, none', () => {
    const { onSelect, onCompare, rerender } = renderPicker()
    fireEvent.change(screen.getByRole('combobox', { name: 'AI analysis version' }), { target: { value: '1' } })
    expect(onSelect).toHaveBeenLastCalledWith(1)
    fireEvent.change(screen.getByRole('combobox', { name: 'AI analysis version' }), { target: { value: '' } })
    expect(onSelect).toHaveBeenLastCalledWith(null)
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare with' }), { target: { value: '1' } })
    expect(onCompare).toHaveBeenLastCalledWith(1)
    rerender(<AIVersionPicker versions={VERSIONS} latestVersion={2} selected={1} onSelect={onSelect} compare={2} onCompare={onCompare} />)
    expect(screen.getByRole('combobox', { name: 'Compare with' })).toHaveValue('2')
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare with' }), { target: { value: 'live' } })
    expect(onCompare).toHaveBeenLastCalledWith('live')
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare with' }), { target: { value: '' } })
    expect(onCompare).toHaveBeenLastCalledWith(null)
  })

  it('an empty history is the first analysis of the run, with no selects', () => {
    const { container } = renderPicker({ versions: [], latestVersion: null })
    expect(container.querySelector('[data-ai-version-empty]')).toHaveTextContent(/^First analysis of this run/)
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('is disabled while the list loads', () => {
    renderPicker({ versions: [], latestVersion: null, loading: true })
    expect(screen.getByRole('combobox', { name: 'AI analysis version' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'Compare with' })).toBeDisabled()
  })
})
