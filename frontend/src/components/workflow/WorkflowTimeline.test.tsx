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

  // Regression: the default-stage selection is synced during render via the
  // previous-value pattern (not a setState-in-effect). Pins that the selected
  // stage still follows the computed default (first running) as the pipeline
  // advances, so the inspector tracks the live stage without a cascading effect.
  it('follows the computed default stage as the pipeline advances', () => {
    const stages = (analysis: string, delivery: string) => [
      { stage_name: 'analysis', status: analysis, label: 'Analysis', route_rationale: 'Analyzing the run' },
      { stage_name: 'delivery', status: delivery, label: 'Delivery', route_rationale: 'Delivering the report' },
    ]
    const { rerender } = render(
      <WorkflowTimeline showInspector stageOrder={['analysis', 'delivery']} stages={stages('running', 'pending')} />,
    )
    // Default = first running = analysis → inspector shows its rationale.
    expect(screen.getAllByText(/Analyzing the run/i).length).toBeGreaterThan(0)

    // Analysis completes, delivery starts running: the default recomputes to
    // delivery and the selection follows it during render.
    rerender(
      <WorkflowTimeline showInspector stageOrder={['analysis', 'delivery']} stages={stages('completed', 'running')} />,
    )
    expect(screen.getAllByText(/Delivering the report/i).length).toBeGreaterThan(0)
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

    // ``getAllByRole('button')`` now includes a Collapse toggle in the
    // component chrome; filter to the stage buttons specifically so the
    // test asserts the stage order regardless of surrounding controls.
    const stageButtons = screen
      .getAllByRole('button')
      .filter(b => /Signal Collection|Delivery/i.test(b.textContent ?? ''))
    expect(stageButtons[0]).toHaveTextContent(/Signal Collection/i)
    expect(stageButtons[1]).toHaveTextContent(/Delivery/i)
  })
})
