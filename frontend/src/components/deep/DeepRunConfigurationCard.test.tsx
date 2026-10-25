/**
 * W14 AGT-07 (`W14-contract.md` §2.5 test 5): "Run configuration" — the pre-scan groups as a selection and the
 * tighten-only controls. Only what the card can see is refused here (nothing selected, more than 500 failures,
 * a group whose member list was cut); the gate's sentence stands where Run on selection would be; a role that
 * cannot trigger sees the groups only.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ESTIMATE_FIXTURE, PRESCAN_FIXTURE } from '@/test/agentOpsMocks'
import type { DeepPreScan } from '@/types/agentOps'
import DeepRunConfigurationCard, { type DeepRunConfigurationCardProps } from './DeepRunConfigurationCard'
import { EMPTY_CONFIG, PRESCAN_EMPTY, PRESCAN_LOAD_ERROR, RUN_CONFIG_TOO_MANY, type DeepRunConfig } from './deepRunModel'

type Props = Partial<DeepRunConfigurationCardProps> & { initial?: DeepRunConfig; changes?: DeepRunConfig[] }

/** The card with its configuration held in state, the way the page holds it. */
function Harness({ initial = EMPTY_CONFIG, changes, ...props }: Props) {
  const [config, setConfig] = useState(initial)
  return (
    <DeepRunConfigurationCard
      preScan={PRESCAN_FIXTURE}
      preScanError={undefined}
      onRetryPreScan={vi.fn()}
      canConfigure
      configuration={ESTIMATE_FIXTURE.configuration}
      onEstimate={vi.fn()}
      estimating={false}
      blockedMessage={null}
      onRun={vi.fn()}
      running={false}
      runNote={null}
      {...props}
      config={config}
      onChange={(next) => {
        changes?.push(next)
        setConfig(next)
      }}
    />
  )
}

const card = () => screen.getByTestId('deep-run-configuration')
const last = <T,>(items: readonly T[]): T | undefined => items[items.length - 1]
const runButton = () => screen.getByRole('button', { name: 'Run on selection' })

describe('DeepRunConfigurationCard', () => {
  it('lists the groups; ticking two says how many failures; Run on selection waits for a selection', () => {
    const onRun = vi.fn()
    render(<Harness onRun={onRun} />)
    expect(within(card()).getByRole('heading', { level: 3, name: 'Run configuration' })).toBeInTheDocument()
    expect(card()).toHaveTextContent('3 signature groups')
    expect(runButton()).toBeDisabled()
    expect(card()).toHaveTextContent('0 groups · 0 failures selected')
    fireEvent.click(screen.getByRole('checkbox', { name: /^Select 7 failures/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /^Select 3 failures/ }))
    expect(card()).toHaveTextContent('2 groups · 10 failures selected')
    expect(runButton()).toBeEnabled()
    fireEvent.click(runButton())
    expect(onRun).toHaveBeenCalledTimes(1)
  })

  it('Select all ticks every group (and clears them again)', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(card()).toHaveTextContent('3 groups · 12 failures selected')
    fireEvent.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(card()).toHaveTextContent('0 groups · 0 failures selected')
  })

  it('more than 500 failures, or a group whose ids were cut, cannot be run: the note says why', () => {
    const big: DeepPreScan = {
      ...PRESCAN_FIXTURE,
      groups: [{ ...PRESCAN_FIXTURE.groups[0], members: 640, members_truncated: true, member_result_ids: PRESCAN_FIXTURE.groups[0].member_result_ids }],
    }
    render(<Harness preScan={big} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /^Select 640 failures/ }))
    expect(runButton()).toBeDisabled()
    expect(runButton()).toHaveAttribute('title', RUN_CONFIG_TOO_MANY)
    expect(screen.getByText(RUN_CONFIG_TOO_MANY)).toHaveAttribute('role', 'note')
  })

  it('the tier offers the allowed tiers; the project\'s own tier tightens nothing', () => {
    const changes: DeepRunConfig[] = []
    render(<Harness changes={changes} />)
    const tier = screen.getByRole('combobox', { name: 'Model tier' })
    expect(Array.from((tier as HTMLSelectElement).options, (o) => o.value)).toEqual(['deterministic', 'slm'])
    expect(tier).toHaveValue('slm')
    fireEvent.change(tier, { target: { value: 'deterministic' } })
    expect(last(changes)?.modelTier).toBe('deterministic')
    fireEvent.change(tier, { target: { value: 'slm' } })
    expect(last(changes)?.modelTier).toBeNull()
  })

  it('the cap never goes above the project\'s; evidence sources narrow (never to none)', () => {
    const changes: DeepRunConfig[] = []
    render(<Harness changes={changes} />)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Failures analysed at most' }), { target: { value: '80' } })
    expect(last(changes)?.maxFailures).toBe(50)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Failures analysed at most' }), { target: { value: '5' } })
    expect(last(changes)?.maxFailures).toBe(5)
    fireEvent.click(screen.getByRole('checkbox', { name: 'logs' }))
    expect(last(changes)?.evidenceSources).toEqual(['history'])
    expect(screen.getByRole('checkbox', { name: 'history' })).toBeDisabled()
  })

  it('a project that does not allow narrowing the tools: the source boxes are disabled with the reason', () => {
    render(<Harness configuration={{ ...ESTIMATE_FIXTURE.configuration, tool_narrowing_allowed: false }} />)
    const logs = screen.getByRole('checkbox', { name: 'logs' })
    expect(logs).toBeDisabled()
    expect(logs.closest('label')).toHaveAttribute('title', 'This project does not allow narrowing the evidence sources')
  })

  it('Estimate re-asks the estimate (no run); the gate\'s sentence replaces Run on selection', () => {
    const onEstimate = vi.fn()
    const gate = 'Deep investigation requires LLM or Auto mode. Current mode: rules.'
    render(<Harness onEstimate={onEstimate} blockedMessage={gate} />)
    fireEvent.click(screen.getByRole('button', { name: 'Estimate' }))
    expect(onEstimate).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Run on selection' })).toBeNull()
    expect(screen.getByTestId('deep-config-gate-note')).toHaveTextContent(gate)
    // Not the page's two `deep-disabled-note`s (their count is pinned by the availability tests and specs).
    expect(screen.queryByTestId('deep-disabled-note')).toBeNull()
  })

  it('a role that cannot trigger sees the groups only', () => {
    render(<Harness canConfigure={false} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Estimate' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run on selection' })).toBeNull()
  })

  it('loading, a failed read and no failing result each say so', () => {
    const retry = vi.fn()
    const { unmount } = render(<Harness preScan={undefined} preScanError={new Error('503')} onRetryPreScan={retry} />)
    expect(card()).toHaveTextContent(PRESCAN_LOAD_ERROR)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledTimes(1)
    unmount()
    const { unmount: unmount2 } = render(<Harness preScan={undefined} />)
    expect(card().querySelector('.animate-spin')).not.toBeNull()
    expect(screen.queryByRole('listitem')).toBeNull()
    unmount2()
    render(<Harness preScan={{ ...PRESCAN_FIXTURE, measured: false, failing_results: 0, groups_total: 0, groups: [] }} />)
    expect(card()).toHaveTextContent(PRESCAN_EMPTY)
    expect(card()).not.toHaveTextContent('0 groups')
  })

  it('shows what the last trigger answered', () => {
    render(<Harness runNote="Deep investigation already in progress" />)
    expect(screen.getByText('Deep investigation already in progress')).toHaveAttribute('role', 'note')
  })
})
