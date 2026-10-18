/**
 * W28 CHAT-01: whether the reader hid the chat's suggested questions, remembered
 * per viewer in this browser (OR-05).
 *
 * Every read and write is wrapped: storage can be blocked (a private window,
 * cleared or blocked site data, an embedded preview), and then the row still
 * shows, and Hide still hides it for the session (the page keeps its own state).
 */
export const HIDE_SUGGESTIONS_KEY = 'tl.chat.hideSuggestions'

/** True only when this browser remembers "hidden" ('1'); anything else, or a storage error, reads false. */
export function readHideSuggestions(): boolean {
  try {
    return window.localStorage.getItem(HIDE_SUGGESTIONS_KEY) === '1'
  } catch {
    return false
  }
}

/** Remembers the choice; a storage error is swallowed (the row still toggles for this session). */
export function writeHideSuggestions(hidden: boolean): void {
  try {
    window.localStorage.setItem(HIDE_SUGGESTIONS_KEY, hidden ? '1' : '0')
  } catch {
    /* storage blocked: the row still toggles */
  }
}
