import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import useSWR from 'swr'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  FileJson,
  GitFork,
  Network,
  PlayCircle,
  RefreshCw,
  Save,
  ShieldCheck,
} from 'lucide-react'
import toast from 'react-hot-toast'

import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import DataUnavailable from '@/components/ui/DataUnavailable'
import { usePermissions } from '@/hooks/usePermissions'
import {
  evaluateWorkflow,
  forkWorkflow,
  listWorkflows,
  publishWorkflow,
  updateWorkflow,
  validateWorkflow,
} from '@/services/workflowService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type {
  WorkflowBody,
  WorkflowEdge,
  WorkflowItem,
  WorkflowValidation,
} from '@/types/workflowDefinition'

interface GraphNode {
  id: string
  agentId: string
  x: number
  y: number
}

interface GraphEdge {
  from: string
  to: string
  loop: boolean
  conditional: boolean
}

export interface WorkflowGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  width: number
  height: number
}

export function buildWorkflowGraph(body: WorkflowBody): WorkflowGraph {
  const stepIds = new Set(body.steps.map(step => step.id))
  const ordinary: GraphEdge[] = body.edges.flatMap((edge: WorkflowEdge) => {
    const sourceList = Array.isArray(edge.from) ? edge.from : [edge.from]
    return sourceList
      .filter(source => stepIds.has(source) && stepIds.has(edge.to))
      .map(source => ({ from: source, to: edge.to, loop: false, conditional: !!edge.when }))
  })
  const loops: GraphEdge[] = body.loops
    .filter(loop => stepIds.has(loop.from) && stepIds.has(loop.to))
    .map(loop => ({ from: loop.from, to: loop.to, loop: true, conditional: true }))

  const layer = new Map(body.steps.map(step => [step.id, 0]))
  for (let pass = 0; pass < body.steps.length; pass += 1) {
    let changed = false
    for (const edge of ordinary) {
      // Invalid draft JSON can contain an ordinary-edge cycle. Keep its
      // preview bounded while the semantic validator reports the cycle.
      const next = Math.min(body.steps.length, (layer.get(edge.from) ?? 0) + 1)
      if (next > (layer.get(edge.to) ?? 0)) {
        layer.set(edge.to, next)
        changed = true
      }
    }
    if (!changed) break
  }

  const byLayer = new Map<number, typeof body.steps>()
  for (const step of body.steps) {
    const column = layer.get(step.id) ?? 0
    byLayer.set(column, [...(byLayer.get(column) ?? []), step])
  }
  const nodes = body.steps.map(step => {
    const column = layer.get(step.id) ?? 0
    const row = (byLayer.get(column) ?? []).findIndex(item => item.id === step.id)
    return { id: step.id, agentId: step.agent_id, x: 30 + column * 220, y: 30 + row * 100 }
  })
  const maxLayer = Math.max(0, ...nodes.map(node => Math.round((node.x - 30) / 220)))
  const maxRows = Math.max(1, ...[...byLayer.values()].map(items => items.length))
  return {
    nodes,
    edges: [...ordinary, ...loops],
    width: Math.max(420, 80 + (maxLayer + 1) * 220),
    height: Math.max(180, 70 + maxRows * 100),
  }
}

function bodyFromItem(item: WorkflowItem): WorkflowBody {
  const definition = item.definition
  return {
    workflow_id: item.workflow_id,
    name: item.name,
    description: item.description,
    base: item.base,
    steps: definition.steps,
    edges: definition.edges ?? [],
    loops: definition.loops ?? [],
    retry_policy: definition.retry_policy,
    review_policy: definition.review_policy,
    deadline_seconds: definition.deadline_seconds,
  }
}

function parseBody(text: string): WorkflowBody {
  const value: unknown = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Workflow JSON must be an object.')
  }
  const candidate = value as Partial<WorkflowBody>
  if (!candidate.workflow_id || !candidate.name || !candidate.base) {
    throw new Error('workflow_id, name, and base are required.')
  }
  if (!Array.isArray(candidate.steps) || candidate.steps.length === 0) {
    throw new Error('steps must contain at least one workflow step.')
  }
  if (!Array.isArray(candidate.edges) || !Array.isArray(candidate.loops)) {
    throw new Error('edges and loops must be arrays.')
  }
  return candidate as WorkflowBody
}

function errorMessage(error: unknown): string {
  const responseDetail = (error as {
    response?: { data?: { detail?: string | { message?: string; errors?: string[] } } }
  })?.response?.data?.detail
  if (typeof responseDetail === 'string') return responseDetail
  if (responseDetail && typeof responseDetail === 'object') {
    return [responseDetail.message, ...(responseDetail.errors ?? [])].filter(Boolean).join(': ')
  }
  return error instanceof Error ? error.message : 'Workflow operation failed'
}

function formatCoverage(coverage: number | null): string {
  return coverage == null ? 'Unmeasured' : `${Math.round(coverage * 100)}% coverage`
}

function ref(item: WorkflowItem): string {
  return `${item.workflow_id}@${item.version}`
}

function GraphPreview({ body }: { body: WorkflowBody }) {
  const graph = useMemo(() => buildWorkflowGraph(body), [body])
  const nodes = new Map(graph.nodes.map(node => [node.id, node]))
  return (
    <div className="overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]">
      <svg
        role="img"
        aria-label={`Workflow graph with ${graph.nodes.length} steps and ${graph.edges.length} edges`}
        viewBox={`0 0 ${graph.width} ${graph.height}`}
        style={{ minWidth: graph.width, height: graph.height }}
      >
        <defs>
          <marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--color-text-faint)" />
          </marker>
        </defs>
        {graph.edges.map((edge, index) => {
          const source = nodes.get(edge.from)
          const target = nodes.get(edge.to)
          if (!source || !target) return null
          return (
            <line
              key={`${edge.from}-${edge.to}-${index}`}
              data-testid="workflow-preview-edge"
              x1={source.x + 160}
              y1={source.y + 28}
              x2={target.x}
              y2={target.y + 28}
              stroke={edge.loop ? 'var(--status-broken)' : 'var(--color-text-faint)'}
              strokeDasharray={edge.loop || edge.conditional ? '6 5' : undefined}
              strokeWidth="1.5"
              markerEnd="url(#workflow-arrow)"
            />
          )
        })}
        {graph.nodes.map(node => (
          <g key={node.id} transform={`translate(${node.x} ${node.y})`}>
            <rect width="160" height="56" rx="8" fill="var(--color-bg-card)" stroke="var(--color-border-light)" />
            <text x="10" y="22" fill="var(--color-text)" fontSize="12" fontWeight="600">{node.id}</text>
            <text x="10" y="41" fill="var(--color-text-muted)" fontSize="9.5">{node.agentId}</text>
          </g>
        ))}
      </svg>
    </div>
  )
}

function ValidationPanel({ validation }: { validation: WorkflowValidation | null }) {
  if (!validation) return null
  return (
    <div
      role="status"
      className="rounded-lg border p-3 text-sm"
      style={{
        borderColor: validation.valid ? 'var(--status-passed-bd)' : 'var(--status-failed-bd)',
        background: validation.valid ? 'var(--status-passed-bg)' : 'var(--status-failed-bg)',
      }}
    >
      <div className="flex items-center gap-2 font-semibold">
        {validation.valid
          ? <CheckCircle2 className="h-4 w-4 text-[var(--status-passed)]" />
          : <AlertTriangle className="h-4 w-4 text-[var(--status-failed)]" />}
        {validation.valid ? 'Semantic validation passed' : 'Semantic validation failed'}
      </div>
      {validation.errors.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-[var(--color-text-secondary)]">
          {validation.errors.map(error => <li key={error}>{error}</li>)}
        </ul>
      )}
    </div>
  )
}

export default function WorkflowEditorPage() {
  const activeProjectId = useProjectStore(state => state.activeProjectId)
  const projectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null
  const { isQaLead } = usePermissions()
  const { data: workflows = [], error, isLoading, mutate } = useSWR(
    projectId ? ['workflow-definitions', projectId] : null,
    () => listWorkflows(projectId as string),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const [selectedRef, setSelectedRef] = useState<string | null>(null)
  const [seededRef, setSeededRef] = useState<string | null>(null)
  const [editorText, setEditorText] = useState('')
  const [savedText, setSavedText] = useState('')
  const [validation, setValidation] = useState<WorkflowValidation | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [forkOpen, setForkOpen] = useState(false)
  const [forkId, setForkId] = useState('wf.')
  const [forkName, setForkName] = useState('')
  const [acceptRegression, setAcceptRegression] = useState(false)
  const [regressionReason, setRegressionReason] = useState('')
  const [evaluationManifestChecksum, setEvaluationManifestChecksum] = useState<string | null>(null)

  const selected = workflows.find(item => ref(item) === selectedRef) ?? workflows[0] ?? null
  const currentRef = selected ? ref(selected) : null
  if (selected && selectedRef == null) setSelectedRef(currentRef)
  if (selected && seededRef !== currentRef) {
    const nextText = JSON.stringify(bodyFromItem(selected), null, 2)
    setSeededRef(currentRef)
    setEditorText(nextText)
    setSavedText(nextText)
    setAcceptRegression(false)
    setRegressionReason('')
    setEvaluationManifestChecksum(null)
  }

  const latestVersions = useMemo(() => {
    const versions = new Map<string, number>()
    for (const item of workflows) {
      versions.set(item.workflow_id, Math.max(item.version, versions.get(item.workflow_id) ?? 0))
    }
    return versions
  }, [workflows])
  const selectedIsLatest = !!selected && latestVersions.get(selected.workflow_id) === selected.version
  const canEdit = !!selected && isQaLead && !selected.built_in && selectedIsLatest
  const dirty = editorText !== savedText
  const parsed = useMemo(() => {
    try {
      return { body: parseBody(editorText), error: null }
    } catch (parseError) {
      return { body: null, error: errorMessage(parseError) }
    }
  }, [editorText])

  function choose(item: WorkflowItem) {
    setSelectedRef(ref(item))
    setSeededRef(null)
    setValidation(null)
    setEvaluationManifestChecksum(null)
    setForkOpen(false)
  }

  async function refreshAndChoose(item: WorkflowItem) {
    await mutate()
    setSelectedRef(ref(item))
    setSeededRef(null)
  }

  async function handleSave() {
    if (!projectId || !selected || !canEdit) return
    if (!parsed.body) {
      toast.error(parsed.error ?? 'Workflow JSON is invalid')
      return
    }
    if (parsed.body.workflow_id !== selected.workflow_id) {
      toast.error('workflow_id cannot be changed while editing; fork the workflow instead.')
      return
    }
    setBusy('save')
    try {
      const saved = await updateWorkflow(projectId, selected.workflow_id, parsed.body)
      const result = await validateWorkflow(projectId, saved.workflow_id, saved.version)
      await refreshAndChoose(saved)
      setValidation(result)
      toast.success(saved.created_version ? `Draft version ${saved.version} created` : 'Draft saved')
    } catch (saveError) {
      toast.error(errorMessage(saveError))
    } finally {
      setBusy(null)
    }
  }

  async function handleValidate(): Promise<WorkflowValidation | null> {
    if (!projectId || !selected) return null
    setBusy('validate')
    try {
      const result = await validateWorkflow(projectId, selected.workflow_id, selected.version)
      setValidation(result)
      if (result.valid) toast.success('Workflow is valid')
      return result
    } catch (validateError) {
      toast.error(errorMessage(validateError))
      return null
    } finally {
      setBusy(null)
    }
  }

  async function handleFork() {
    if (!projectId || !selected || !isQaLead) return
    setBusy('fork')
    try {
      const forked = await forkWorkflow(projectId, selected.workflow_id, {
        workflow_id: forkId.trim(),
        name: forkName.trim(),
        description: selected.description,
      }, selected.version)
      await refreshAndChoose(forked)
      setValidation(null)
      setForkOpen(false)
      toast.success('Workflow fork created')
    } catch (forkError) {
      toast.error(errorMessage(forkError))
    } finally {
      setBusy(null)
    }
  }

  async function handleEvaluate() {
    if (!projectId || !selected || selected.built_in || !isQaLead || dirty) return
    setBusy('evaluate')
    try {
      const result = await evaluateWorkflow(projectId, selected.workflow_id, selected.version)
      setEvaluationManifestChecksum(result.manifest_checksum)
      await mutate()
      toast.success(`Evaluation ${result.verdict}: ${Math.round(result.coverage * 100)}% coverage`)
    } catch (evaluationError) {
      toast.error(errorMessage(evaluationError))
    } finally {
      setBusy(null)
    }
  }

  async function handlePublish() {
    if (!projectId || !selected || !canEdit || dirty) return
    setBusy('publish')
    try {
      const result = await validateWorkflow(projectId, selected.workflow_id, selected.version)
      setValidation(result)
      if (!result.valid) {
        toast.error('Fix semantic validation errors before publishing.')
        return
      }
      const published = await publishWorkflow(projectId, selected.workflow_id, {
        version: selected.version,
        definition_sha256: selected.definition_sha256,
        accept_regression: acceptRegression,
        reason: acceptRegression ? regressionReason.trim() : null,
        eval_manifest_checksum: acceptRegression ? evaluationManifestChecksum : null,
      })
      await refreshAndChoose(published)
      setValidation(null)
      toast.success(`Published ${published.workflow_id}@${published.version}`)
    } catch (publishError) {
      toast.error(errorMessage(publishError))
    } finally {
      setBusy(null)
    }
  }

  if (!projectId) {
    return (
      <ProjectRequiredEmptyState
        icon={<Network className="h-10 w-10" />}
        description="Workflow definitions, evaluation evidence, and publish authority belong to one project."
      />
    )
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Workflow editor"
        subtitle="Fork built-in pipelines, edit versioned definitions, preview topology, validate, evaluate, and publish."
        actions={(
          <Link to="/agents" className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">
            <ArrowLeft className="h-4 w-4" /> Agent pipeline
          </Link>
        )}
      />

      {error ? (
        <DataUnavailable
          error={error}
          onRetry={() => void mutate()}
          testId="workflow-data-unavailable"
        />
      ) : isLoading ? <LoadingSpinner size="lg" /> : (
        <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="space-y-3">
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Definitions</div>
              <div className="space-y-1.5">
                {workflows.map(item => {
                  const active = selected ? ref(item) === ref(selected) : false
                  return (
                    <button
                      key={ref(item)}
                      type="button"
                      onClick={() => choose(item)}
                      className="w-full rounded-lg border p-3 text-left transition-colors"
                      style={{
                        borderColor: active ? 'var(--color-accent)' : 'var(--color-border)',
                        background: active ? 'var(--color-accent-muted)' : 'var(--color-bg-secondary)',
                      }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-semibold text-[var(--color-text)]">{item.name}</span>
                        <span className="font-mono text-[10px] text-[var(--color-text-muted)]">v{item.version}</span>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10.5px] text-[var(--color-text-muted)]">
                        <span>{item.workflow_id}</span>
                        <span>·</span>
                        <span>{item.built_in ? 'built-in' : item.status}</span>
                        <span>·</span>
                        <span>{formatCoverage(item.eval_coverage)}</span>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
            {!isQaLead && (
              <p className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 text-xs text-[var(--color-text-muted)]">
                You can inspect definitions and validation. QA Lead access is required to fork, edit, evaluate, or publish.
              </p>
            )}
          </aside>

          {selected ? (
            <main className="min-w-0 space-y-4">
              <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold text-[var(--color-text)]">{selected.name}</h2>
                      <span className="rounded bg-[var(--color-bg-secondary)] px-2 py-0.5 font-mono text-[10px] text-[var(--color-text-muted)]">{ref(selected)}</span>
                      <span className="rounded bg-[var(--color-bg-secondary)] px-2 py-0.5 text-[10px] uppercase text-[var(--color-text-muted)]">{selected.status}</span>
                    </div>
                    <p className="mt-1 text-sm text-[var(--color-text-muted)]">{selected.description || 'No description.'}</p>
                    <div className="mt-2 flex flex-wrap gap-3 text-xs text-[var(--color-text-muted)]">
                      <span>Base: <strong className="text-[var(--color-text-secondary)]">{selected.base}</strong></span>
                      <span>Evaluation: <strong className="text-[var(--color-text-secondary)]">{selected.eval_verdict ?? 'unmeasured'}</strong></span>
                      <span data-testid="workflow-eval-coverage">{formatCoverage(selected.eval_coverage)}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {isQaLead && (
                      <button type="button" onClick={() => setForkOpen(value => !value)} className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border)] px-3 py-1.5 text-xs hover:bg-[var(--color-bg-hover)]">
                        <GitFork className="h-3.5 w-3.5" /> Fork workflow
                      </button>
                    )}
                    <button type="button" onClick={handleValidate} disabled={busy !== null || dirty} title={dirty ? 'Save changes before validating the stored version' : undefined} className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border)] px-3 py-1.5 text-xs disabled:opacity-50 hover:bg-[var(--color-bg-hover)]">
                      <ShieldCheck className="h-3.5 w-3.5" /> Validate
                    </button>
                    {isQaLead && !selected.built_in && selected.status === 'draft' && (
                      <button type="button" onClick={handleEvaluate} disabled={busy !== null || dirty} className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border)] px-3 py-1.5 text-xs disabled:opacity-50 hover:bg-[var(--color-bg-hover)]">
                        <PlayCircle className="h-3.5 w-3.5" /> Run evaluation
                      </button>
                    )}
                  </div>
                </div>

                {forkOpen && (
                  <div className="mt-4 grid gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3 md:grid-cols-[1fr_1fr_auto]">
                    <label className="text-xs text-[var(--color-text-muted)]">
                      New workflow ID
                      <input aria-label="New workflow ID" value={forkId} onChange={event => setForkId(event.target.value)} className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-sm" />
                    </label>
                    <label className="text-xs text-[var(--color-text-muted)]">
                      Fork name
                      <input aria-label="Fork name" value={forkName} onChange={event => setForkName(event.target.value)} className="mt-1 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm" />
                    </label>
                    <button type="button" onClick={handleFork} disabled={busy !== null || !forkId.trim() || !forkName.trim()} className="self-end rounded bg-[var(--color-btn-primary-bg)] px-3 py-1.5 text-sm text-[var(--color-btn-primary-text)] disabled:opacity-50">
                      Create fork
                    </button>
                  </div>
                )}
              </section>

              <section className="grid gap-4 2xl:grid-cols-2">
                <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <FileJson className="h-4 w-4 text-[var(--color-text-muted)]" />
                      <h3 className="text-sm font-semibold text-[var(--color-text)]">Definition JSON</h3>
                    </div>
                    {dirty && <span className="text-[10px] uppercase tracking-wider text-[var(--status-broken)]">Unsaved</span>}
                  </div>
                  <textarea
                    aria-label="Workflow definition JSON"
                    value={editorText}
                    onChange={event => {
                      setEditorText(event.target.value)
                      setValidation(null)
                      setEvaluationManifestChecksum(null)
                    }}
                    readOnly={!canEdit}
                    spellCheck={false}
                    className="h-[430px] w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-3 font-mono text-xs leading-relaxed text-[var(--color-text-secondary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] read-only:opacity-75"
                  />
                  {!selectedIsLatest && !selected.built_in && (
                    <p className="mt-2 text-xs text-[var(--color-text-muted)]">Historical versions are read-only. Select the newest version to edit.</p>
                  )}
                  {selected.built_in && (
                    <p className="mt-2 text-xs text-[var(--color-text-muted)]">Built-in definitions are immutable. Fork this version to customize it.</p>
                  )}
                  {parsed.error && (
                    <p role="alert" className="mt-2 text-xs text-[var(--status-failed)]">{parsed.error}</p>
                  )}
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={handleSave}
                      disabled={!canEdit || !dirty || busy !== null || !!parsed.error}
                      className="inline-flex items-center gap-1.5 rounded bg-[var(--color-btn-primary-bg)] px-3 py-1.5 text-sm text-[var(--color-btn-primary-text)] disabled:opacity-50"
                    >
                      {busy === 'save' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                      {selected.status === 'published' ? 'Save as new draft' : 'Save draft'}
                    </button>
                    {selected.status === 'draft' && canEdit && (
                      <button type="button" onClick={handlePublish} disabled={busy !== null || dirty || (acceptRegression && (!regressionReason.trim() || !evaluationManifestChecksum))} className="inline-flex items-center gap-1.5 rounded border border-[var(--status-passed-bd)] bg-[var(--status-passed-bg)] px-3 py-1.5 text-sm text-[var(--status-passed)] disabled:opacity-50">
                        <ShieldCheck className="h-4 w-4" /> Publish
                      </button>
                    )}
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4">
                    <div className="mb-3 flex items-center gap-2">
                      <Network className="h-4 w-4 text-[var(--color-text-muted)]" />
                      <h3 className="text-sm font-semibold text-[var(--color-text)]">Graph preview</h3>
                    </div>
                    {parsed.body ? <GraphPreview body={parsed.body} /> : (
                      <div className="grid h-44 place-items-center rounded-lg border border-dashed border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
                        Fix the JSON to preview this workflow.
                      </div>
                    )}
                  </div>
                  <ValidationPanel validation={validation} />
                  {selected.eval_verdict === 'fail' && canEdit && (
                    <div className="rounded-lg border border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] p-4">
                      <label className="flex items-start gap-2 text-sm text-[var(--color-text-secondary)]">
                        <input type="checkbox" checked={acceptRegression} onChange={event => setAcceptRegression(event.target.checked)} className="mt-0.5" />
                        Accept the measured regression for this publication
                      </label>
                      {acceptRegression && (
                        <textarea aria-label="Regression acceptance reason" value={regressionReason} onChange={event => setRegressionReason(event.target.value)} placeholder="Required QA Lead reason" className="mt-3 h-20 w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-2 text-sm" />
                      )}
                    </div>
                  )}
                </div>
              </section>
            </main>
          ) : (
            <div className="grid min-h-64 place-items-center rounded-lg border border-dashed border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
              No workflow definitions are available.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
