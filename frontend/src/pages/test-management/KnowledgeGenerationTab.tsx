import { useState } from 'react'
import { Bot, Loader2, Search, Settings, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { useKnowledgeSources, useRagStatus } from '@/hooks/useGenerationBatch'
import KnowledgeSourcePicker from '@/components/rag/KnowledgeSourcePicker'
import GenerationReviewPanel from '@/components/rag/GenerationReviewPanel'
import { ragService } from '@/services/ragGenerationService'
import type { RagGenerateResponse, RetrievedChunk } from '@/types/rag-generation'
import EmptyState from '@/components/ui/EmptyState'

export default function KnowledgeGenerationTab() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId

  const { data: ragStatus } = useRagStatus()
  const { data: sourcesData, isLoading: sourcesLoading } = useKnowledgeSources(projectId)

  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const [promptText, setPromptText] = useState('')
  const [retrievedChunks, setRetrievedChunks] = useState<RetrievedChunk[]>([])
  const [generating, setGenerating] = useState(false)
  const [retrieving, setRetrieving] = useState(false)
  const [result, setResult] = useState<RagGenerateResponse | null>(null)

  if (!projectId) {
    return <EmptyState icon={<Search className="h-8 w-8" />} title="Select a project" description="Choose a project from the selector to use knowledge generation." />
  }

  if (ragStatus && !ragStatus.enabled) {
    return (
      <div className="card flex items-center gap-3 border-[var(--status-broken-bd)]/30 bg-[var(--status-broken-bg)]/10 py-4 px-5">
        <Bot className="h-5 w-5 text-[var(--status-broken)]" />
        <div>
          <p className="text-sm font-medium text-[var(--status-broken)]">Knowledge RAG is not enabled</p>
          <p className="text-xs text-[var(--status-broken)]/70 mt-0.5">
            An admin can enable this feature from{' '}
            <Link to="/settings/ai" className="underline text-[var(--status-broken)] hover:text-[var(--status-broken)] inline-flex items-center gap-1">
              <Settings className="h-3 w-3" />Settings &gt; AI Configuration
            </Link>.
          </p>
        </div>
      </div>
    )
  }

  const handleToggleSource = (id: string) => {
    setSelectedSourceIds(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id],
    )
  }

  const handleSyncSource = async (id: string) => {
    try {
      await ragService.syncSource(id)
      toast.success('Sync triggered')
    } catch {
      toast.error('Sync failed')
    }
  }

  const handleRetrieve = async () => {
    if (!promptText.trim() && selectedSourceIds.length === 0) {
      toast.error('Enter a query or select sources')
      return
    }
    setRetrieving(true)
    try {
      const res = await ragService.retrieve({
        project_id: projectId,
        query_text: promptText || 'test case requirements',
        source_ids: selectedSourceIds.length > 0 ? selectedSourceIds : undefined,
        top_k: 10,
      })
      setRetrievedChunks(res.chunks)
      toast.success(`Retrieved ${res.total} chunks`)
    } catch {
      toast.error('Retrieval failed')
    } finally {
      setRetrieving(false)
    }
  }

  const handleGenerate = async () => {
    setGenerating(true)
    setResult(null)
    try {
      const res = await ragService.generate({
        project_id: projectId,
        prompt_text: promptText,
        source_ids: selectedSourceIds,
        persist: true,
      })
      setResult(res)
      toast.success(`Generated ${res.test_cases.length} test cases`)
    } catch (err: unknown) {
      // The api interceptor already shows a toast for non-422/404 errors,
      // so only add context when the interceptor message is generic.
      const axiosErr = err as { response?: { data?: { detail?: string } }; message?: string }
      if (!axiosErr.response) {
        // Network error — no response received (timeout, connection refused, etc.)
        toast.error('Generation request timed out or backend unreachable. Is the LLM service running?')
      }
      // else: api interceptor already showed the error detail toast
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Source picker */}
      <section className="card space-y-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider">
          Knowledge Sources
        </h3>
        <KnowledgeSourcePicker
          sources={sourcesData?.items ?? []}
          selectedIds={selectedSourceIds}
          onToggle={handleToggleSource}
          onSync={handleSyncSource}
          loading={sourcesLoading}
        />
      </section>

      {/* Prompt input */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider">
          Generation Prompt
        </h3>
        <textarea
          className="input w-full h-32 resize-y text-sm"
          placeholder="Describe what test cases to generate, or leave empty to generate from selected sources..."
          value={promptText}
          onChange={e => setPromptText(e.target.value)}
          maxLength={5000}
        />
        <div className="flex items-center gap-3">
          <button
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] transition-colors"
            onClick={handleRetrieve}
            disabled={retrieving}
          >
            {retrieving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
            Preview Evidence
          </button>
          <button
            className="btn-primary flex items-center gap-2"
            onClick={handleGenerate}
            disabled={generating || (selectedSourceIds.length === 0 && !promptText.trim())}
          >
            {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            Generate Test Cases
          </button>
          <span className="text-xs text-[var(--color-text-faint)] ml-auto">
            {selectedSourceIds.length} source{selectedSourceIds.length !== 1 ? 's' : ''} selected
          </span>
        </div>
      </section>

      {/* Retrieved chunks preview */}
      {retrievedChunks.length > 0 && !result && (
        <section className="card space-y-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider">
            Retrieved Evidence ({retrievedChunks.length})
          </h3>
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {retrievedChunks.map((chunk, i) => (
              <div key={i} className="rounded border border-[var(--color-border)] px-3 py-2 text-xs">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-medium text-[var(--color-text)]">{chunk.source_title}</span>
                  {chunk.section_heading && (
                    <span className="text-[var(--color-text-muted)]">{chunk.section_heading}</span>
                  )}
                  <span className="text-[var(--color-text-faint)] ml-auto">
                    {(chunk.relevance_score * 100).toFixed(0)}%
                  </span>
                </div>
                <p className="text-[var(--color-text-muted)] line-clamp-3">{chunk.chunk_text}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Generation results */}
      {result && (
        <section className="card space-y-4">
          <h3 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider">
            Generated Test Cases — Review
          </h3>
          <GenerationReviewPanel result={result} />
        </section>
      )}
    </div>
  )
}
