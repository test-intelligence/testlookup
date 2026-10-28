/**
 * Inbox › Activity's copy and pure rules (W21d ACT-02), so the tab and its
 * tests read one string. The tab lists what the reader did and what involves
 * them (mentions, test cases they are assigned to or wrote, defects assigned
 * to them) across their projects, from the server's `/activity/mine` read.
 */
import type { ChipOption } from './ChipFilter'
import type { MineView } from '@/types/activityLive'
import { formatNumber } from '@/utils/formatters'

/** `Mine · Involving me · All`; the default is All. */
export const VIEW_OPTIONS: readonly ChipOption<MineView>[] = [
  { id: 'mine', label: 'Mine', title: 'What you did while signed in' },
  {
    id: 'involving',
    label: 'Involving me',
    title: 'Mentions of you, test cases you are assigned to or wrote, defects assigned to you',
  },
  { id: 'all', label: 'All', title: 'Both: what you did and what involves you' },
]

export const DEFAULT_VIEW: MineView = 'all'

/** A failed first read: an outage, never an empty inbox. */
export const LOAD_ERROR = 'Could not load the activity.'
/** A failed later page, beside the rows that did load. */
export const LOAD_OLDER_ERROR = 'Could not load older activity'

/** "24 hours" for one day, "7 days" otherwise. */
export function windowPhrase(days: number): string {
  return days === 1 ? '24 hours' : `${days} days`
}

/** The count line beside the toolbar: the server's `total`, never the page length. */
export function countLine(total: number, days: number): string {
  return `${formatNumber(total)} event${total === 1 ? '' : 's'} in the last ${windowPhrase(days)}`
}

/**
 * The empty list's sentence. `projects === 0`: the reader belongs to no
 * project, so nothing could be listed — said so, not "nothing happened".
 */
export function emptySentence(view: MineView, projects: number | null, days: number): string {
  if (projects === 0) return 'You are not a member of any project yet.'
  const window = windowPhrase(days)
  if (view === 'mine') return `Nothing you did in the last ${window}.`
  if (view === 'involving') return `Nothing involving you in the last ${window}.`
  return `No activity in the last ${window}.`
}
