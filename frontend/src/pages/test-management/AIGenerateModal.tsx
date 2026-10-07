import { useState } from 'react'
import { Clock, Sparkles } from 'lucide-react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import Tabs from '@/components/ui/Tabs'
import { testManagementService } from '@/services/testManagementService'
import KnowledgeGenerationTab from './KnowledgeGenerationTab'
import { ModalWrap } from './tmUi'

/**
 * AI generation, one entry point (UX redesign P4 item 7). Two sources:
 *
 *  - From a description: a requirement, a module or code path, or a defect,
 *    described in words; generated in the background and saved as drafts.
 *    (The Cases tab's "Generate test cases" card offered these three as
 *    separate paths, each opening this same form.)
 *  - From documents: the knowledge-base generation flow (pick sources,
 *    preview evidence, generate, review) — the same component as the More ▾
 *    › Knowledge tab, so it behaves identically in both places.
 */
export type GenerateSource = 'description' | 'documents'

interface AIGenerateModalProps {
  projectId: string
  onClose: () => void
  /** Which source the modal opens on (default: a description). */
  initialSource?: GenerateSource
}

export default function AIGenerateModal({ projectId, onClose, initialSource = 'description' }: AIGenerateModalProps) {
  const [source, setSource] = useState<GenerateSource>(initialSource)
  const [requirements, setRequirements] = useState('')
  const [loading, setLoading] = useState(false)

  const handleGenerate = async () => {
    if (requirements.trim().length < 3) { toast.error('Please enter at least 3 characters'); return }
    setLoading(true)
    try {
      await testManagementService.aiGenerateAsync({
        project_id: projectId,
        requirements: requirements.trim(),
        persist: true,
      })
      toast.success(
        'Test cases are being generated in the background and will be saved as drafts — refresh the list in a moment.',
        { duration: 7000 }
      )
      onClose()
    } catch {
      toast.error('Failed to start AI generation')
      setLoading(false)
    }
  }

  return (
    <ModalWrap onClose={onClose} title="AI Generate Test Cases" width={source === 'documents' ? 'max-w-4xl' : 'max-w-2xl'}>
      <div className="space-y-4">
        <Tabs<GenerateSource>
          ariaLabel="Generate from"
          value={source}
          onChange={setSource}
          items={[
            { id: 'description', label: 'From a description' },
            { id: 'documents', label: 'From documents' },
          ]}
        />
        {source === 'description' ? (
          <>
            <p className="text-sm text-[var(--color-text-muted)]">
              Describe a requirement to cover, a module or code path to exercise, or a defect to write
              regression cases for. The AI will generate test cases and save them as drafts in your
              review queue.
            </p>
            <textarea
              className="input w-full h-36 resize-none"
              value={requirements}
              onChange={e => setRequirements(e.target.value)}
              placeholder="e.g. User should be able to log in with email and password, with form validation and error handling for wrong credentials..."
              aria-label="What to generate test cases for"
              disabled={loading}
              autoFocus
            />
            <div className="bg-[var(--color-bg-secondary)]/80 rounded-lg px-3 py-2 flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
              <Clock className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-[var(--color-text-muted)]" />
              <span>Generation runs in the background (typically 1–2 minutes). You can close this and check the list shortly.</span>
            </div>
            <div className="flex justify-end gap-3 pt-1">
              <button onClick={onClose} disabled={loading} className="btn-secondary">Cancel</button>
              <button onClick={handleGenerate} disabled={loading || requirements.trim().length < 3} className="btn-primary flex items-center gap-2">
                {loading ? <LoadingSpinner size="sm" /> : <Sparkles className="h-4 w-4" />}
                {loading ? 'Submitting…' : 'Generate & Save'}
              </button>
            </div>
          </>
        ) : (
          <div data-generate-source="documents">
            <KnowledgeGenerationTab />
          </div>
        )}
      </div>
    </ModalWrap>
  )
}
