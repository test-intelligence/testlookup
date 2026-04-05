import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import WorkflowTimeline from './WorkflowTimeline'

describe('WorkflowTimeline', () => {
  it('renders stages and lets the user inspect a selected stage', () => {
    render(
      <WorkflowTimeline
        title="Workflow Progress"
        stages={[
          {
            stage_name: 'ingestion',
            status: 'completed',
            started_at: '2026-04-01T10:00:00Z',
            completed_at: '2026-04-01T10:00:05Z',
            execution_path: 'executed',
            confidence_score: 90,
            evidence_count: 2,
            cost_usd: 0.002,
            input_tokens: 10,
            output_tokens: 20,
            total_tokens: 30,
            llm_calls_count: 1,
            route_rationale: 'Loaded run data',
          },
        ]}
        events={[
          {
            event_type: 'stage_started',
            stage_name: 'ingestion',
            timestamp: '2026-04-01T10:00:00Z',
            detail: { message: 'Starting ingestion' },
          },
        ]}
        showInspector
        showEventFeed
      />,
    )

    expect(screen.getByText('Workflow Progress')).toBeInTheDocument()
    expect(screen.getAllByText(/Ingestion/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/Workflow Event Feed/i)).toBeInTheDocument()
    expect(screen.getByText(/Starting ingestion/i)).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: /Ingestion/i })[0])
    expect(screen.getByText(/Loaded run data/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Evidence/i).length).toBeGreaterThan(0)
  })

  it('shows an empty state when there are no stages', () => {
    render(<WorkflowTimeline stages={[]} />)

    expect(screen.getByText(/No workflow stages found/i)).toBeInTheDocument()
  })

  it('respects a custom stage order for synthesized workflows', () => {
    render(
      <WorkflowTimeline
        stages={[
          {
            stage_name: 'delivery',
            status: 'completed',
            label: 'Delivery',
            description: 'Deliver the report',
          },
          {
            stage_name: 'signal_collection',
            status: 'completed',
            label: 'Signal Collection',
            description: 'Collect signals',
          },
        ]}
        stageOrder={['signal_collection', 'delivery']}
      />,
    )

    const buttons = screen.getAllByRole('button')
    expect(buttons[0]).toHaveTextContent(/Signal Collection/i)
    expect(buttons[1]).toHaveTextContent(/Delivery/i)
  })
})
