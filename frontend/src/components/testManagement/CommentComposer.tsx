/**
 * W21d ACT-02: the test case's comment box, with `@mention` suggestions.
 *
 * Moved out of `TestManagementPage`'s Comments tab unchanged (the textarea, its
 * `Add a comment…` placeholder, `Post Comment`, `Comment added` /
 * `Failed to add comment`), plus one thing: while the caret sits in an `@token`
 * an inline list under the textarea offers the case's project members. It reads
 * the members the page already reads (`useProjectMembers`, the same SWR key —
 * no new request path), offers only the server's eligible members other than
 * the signed-in user, and a click inserts `@username ` (never an e-mail or an
 * id). The list is in the flow (no portal, no popover, no dialog) and every
 * name is React text.
 *
 * The server resolves the mentions and answers who it notified; the toast says
 * so (`Comment added · notified @alice`).
 *
 * Imports only what the page already imports, plus the pure `mentionModel`, so
 * the page's module mocks keep working.
 */
import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useProjectMembers } from '@/hooks/useUserManagement'
import { testManagementService } from '@/services/testManagementService'
import { useAuthStore } from '@/store/authStore'
import {
  activeMentionQuery,
  insertMention,
  mentionOptions,
  postedToast,
  type PostedMention,
} from './mentionModel'

interface CommentComposerProps {
  caseId: string
  projectId: string
  /** Called after a comment posted (the page re-reads its comments). */
  onPosted: () => void
}

export default function CommentComposer({ caseId, projectId, onPosted }: CommentComposerProps) {
  const [comment, setComment] = useState('')
  const [caret, setCaret] = useState(0)
  const [addingComment, setAddingComment] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const pendingCaret = useRef<number | null>(null)
  const selfId = useAuthStore((s) => s.user?.id ?? null)
  const { data: members } = useProjectMembers(projectId || null)

  const token = activeMentionQuery(comment, caret)
  const options = token ? mentionOptions(Array.isArray(members) ? members : [], token.query, selfId) : []

  // After an insert, put the caret right after the inserted name.
  useEffect(() => {
    const pos = pendingCaret.current
    const el = textarea.current
    if (pos === null || !el) return
    pendingCaret.current = null
    el.focus()
    el.setSelectionRange(pos, pos)
  }, [comment])

  const pick = (username: string) => {
    if (!token) return
    const next = insertMention(comment, token.start, caret, username)
    pendingCaret.current = next.caret
    setComment(next.text)
    setCaret(next.caret)
  }

  const handleAddComment = async () => {
    if (!comment.trim()) return
    setAddingComment(true)
    try {
      const posted = await testManagementService.addComment(caseId, { content: comment.trim(), comment_type: 'general' })
      setComment('')
      setCaret(0)
      onPosted()
      toast.success(postedToast((posted as { mentions?: PostedMention[] } | undefined)?.mentions))
    } catch {
      toast.error('Failed to add comment')
    } finally {
      setAddingComment(false)
    }
  }

  return (
    <div data-comment-composer="" className="border-t border-[var(--color-border)] pt-3">
      <textarea
        ref={textarea}
        className="input w-full h-20 resize-none text-sm"
        value={comment}
        onChange={e => {
          setComment(e.target.value)
          setCaret(e.target.selectionStart ?? e.target.value.length)
        }}
        onSelect={e => setCaret(e.currentTarget.selectionStart ?? 0)}
        placeholder="Add a comment…"
      />
      {options.length > 0 && (
        <ul
          data-mention-suggestions=""
          aria-label="Mention a project member"
          className="mt-1 mb-0 p-1 list-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)]"
        >
          {options.map(option => (
            <li key={option.username}>
              <button
                type="button"
                data-mention-option={option.username}
                title={option.label}
                onMouseDown={e => e.preventDefault()}
                onClick={() => pick(option.username)}
                className="block w-full truncate rounded px-2 py-1 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]"
              >
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex justify-end mt-2">
        <button onClick={handleAddComment} disabled={addingComment || !comment.trim()} className="btn-primary text-sm flex items-center gap-2">
          {addingComment && <LoadingSpinner size="sm" />}
          Post Comment
        </button>
      </div>
    </div>
  )
}
