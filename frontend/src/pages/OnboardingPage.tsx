import { useNavigate } from 'react-router-dom'
import {
  CheckCircle, Circle, FlaskConical, GitBranch, Brain, Key, Radio, SkipForward,
} from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { onboardingService, type OnboardingStep } from '@/services/onboardingService'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { useOnboardingStatus } from '@/hooks/useOnboardingStatus'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildOnboardingWorkflow } from '@/components/workflow/workflowPresets'

const STEP_ICONS: Record<string, React.ElementType> = {
  create_project: FlaskConical,
  upload_run: GitBranch,
  connect_jira: Key,
  connect_telemetry: Radio,
  view_intelligence: Brain,
}

const STEP_LINKS: Record<string, string> = {
  create_project: '/projects',
  upload_run: '/runs',
  connect_jira: '/settings/integrations',
  connect_telemetry: '/settings/integrations',
  view_intelligence: '/intelligence',
}

function StepCard({
  step,
  onSkip,
  canSkip = true,
}: {
  step: OnboardingStep
  onSkip: () => void
  canSkip?: boolean
}) {
  const navigate = useNavigate()
  const Icon = STEP_ICONS[step.key] ?? Circle
  const isDone = step.status === 'completed'
  const isSkipped = step.status === 'skipped'

  return (
    <div className={clsx(
      'card flex items-center gap-4 transition-all',
      isDone && 'border-emerald-700/40 bg-emerald-900/10',
      isSkipped && 'opacity-50',
    )}>
      <div className={clsx(
        'p-2.5 rounded-xl shrink-0',
        isDone ? 'bg-emerald-600/20' : 'bg-[var(--color-bg-secondary)]',
      )}>
        {isDone
          ? <CheckCircle className="h-5 w-5 text-emerald-400" />
          : <Icon className="h-5 w-5 text-[var(--color-text-muted)]" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className={clsx('font-medium', isDone ? 'text-emerald-300' : 'text-[var(--color-text)]')}>{step.label}</p>
        <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{step.description}</p>
        {step.completed_at && (
          <p className="text-[10px] text-[var(--color-text-faint)] mt-1">
            Completed {new Date(step.completed_at).toLocaleDateString()}
          </p>
        )}
      </div>
      {!isDone && !isSkipped && (
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => navigate(STEP_LINKS[step.key] ?? '/overview')}
            className="px-3 py-1.5 text-xs bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] rounded-lg font-medium transition-colors"
          >
            Set Up
          </button>
          {canSkip && (
            <button
              onClick={onSkip}
              className="p-1.5 text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] transition-colors"
              title="Skip this step"
            >
              <SkipForward className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      )}
      {isDone && !isSkipped && (
        <button
          onClick={() => navigate(STEP_LINKS[step.key] ?? '/overview')}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors shrink-0"
        >
          View →
        </button>
      )}
    </div>
  )
}

export default function OnboardingPage() {
  const navigate = useNavigate()
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const isAllProjects = activeProjectId === ALL_PROJECTS_ID
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId
  const { status, isLoading: loading, mutate } = useOnboardingStatus(projectId)
  const workflow = buildOnboardingWorkflow(status)

  if (!projectId && !isAllProjects) {
    return (
      <div className="space-y-6">
        <PageHeader title="Getting Started" subtitle="Set up your project for AI-powered test intelligence" />
        <div className="flex flex-col items-center py-20 text-[var(--color-text-muted)]">
          <FlaskConical className="h-10 w-10 mb-3 text-[var(--color-text-faint)]" />
          <p className="font-medium">Select a project first</p>
          <p className="text-sm mt-1">Choose a project from the sidebar, then come back to complete setup.</p>
          <button onClick={() => navigate('/projects')} className="mt-4 btn-primary text-sm">
            Go to Projects
          </button>
        </div>
      </div>
    )
  }

  if (loading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>

  const steps = status?.steps ?? []
  const progressPct = status?.progress_pct ?? 0

  async function handleSkip(stepKey: string) {
    if (!projectId) return
    try {
      const updated = await onboardingService.skipStep(projectId, stepKey)
      mutate(updated, { revalidate: false })
    } catch {
      toast.error('Failed to skip step')
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Getting Started"
        subtitle={isAllProjects
          ? 'Workspace onboarding for new projects — create a project, connect data, and activate Run Intelligence'
          : 'Complete these steps to unlock the full power of AI test intelligence'}
      />

      {isAllProjects && (
        <div className="card border border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]/30 flex items-center justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[var(--color-text-secondary)]">Workspace onboarding</p>
            <p className="text-sm text-[var(--color-text-secondary)] mt-1">
              All Projects is selected. Use this flow to start onboarding a new project, connect integrations, and get to Run Intelligence quickly.
            </p>
          </div>
          <button onClick={() => navigate('/projects')} className="btn-primary text-sm shrink-0">
            Create Project
          </button>
        </div>
      )}

      <WorkflowTimeline
        title="Activation workflow"
        subtitle="Create a project, connect data, and unlock Run Intelligence"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      {/* Progress bar */}
      <div className="card">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-[var(--color-text-secondary)] font-medium">Setup Progress</span>
          <span className="text-sm font-bold text-[var(--color-text)]">{progressPct}%</span>
        </div>
        <div className="w-full h-2 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
          <div
            className={clsx(
              'h-full rounded-full transition-all duration-500',
              progressPct === 100 ? 'bg-emerald-500' : 'bg-neutral-300',
            )}
            style={{ width: `${progressPct}%` }}
          />
        </div>
        {status?.is_complete && (
          <p className="text-xs text-emerald-400 mt-2">
            Setup complete — you're ready to use all features.
          </p>
        )}
      </div>

      {/* Steps */}
      <div className="space-y-3">
        {steps.map(step => (
          <StepCard
            key={step.key}
            step={step}
            canSkip={!isAllProjects && !!projectId}
            onSkip={() => handleSkip(step.key)}
          />
        ))}
      </div>

      {/* Done CTA */}
      {status?.is_complete && (
        <div className="flex justify-center pt-4">
          <button
            onClick={() => navigate('/intelligence')}
            className="btn-primary flex items-center gap-2 text-sm"
          >
            <Brain className="h-4 w-4" />
            Go to Run Intelligence
          </button>
        </div>
      )}
    </div>
  )
}
