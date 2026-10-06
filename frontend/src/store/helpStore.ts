/**
 * Which help topic is open (UX redesign). `PageHeader`'s **?** button calls
 * `openHelp(topic)`; the help drawer (P1) renders whatever is open. A topic is
 * a documentation page id (`content/guide/manifest.ts`), optionally with an
 * anchor inside it.
 */
import { create } from 'zustand'

interface HelpState {
  /** The open topic, or `null` when help is closed. */
  topic: string | null
  /** An anchor inside the topic's page, if any. */
  anchor: string | null
  open: (topic: string, anchor?: string | null) => void
  close: () => void
}

export const useHelpStore = create<HelpState>()((set) => ({
  topic: null,
  anchor: null,
  open: (topic, anchor = null) => set({ topic, anchor }),
  close: () => set({ topic: null, anchor: null }),
}))

/** Open help on `topic` from anywhere (no hook needed). */
export function openHelp(topic: string, anchor?: string | null): void {
  useHelpStore.getState().open(topic, anchor)
}
