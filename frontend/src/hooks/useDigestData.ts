import useSWR from 'swr'
import { type DigestSubscription, listSubscriptions } from '@/services/digestService'
import { type SavedView, listSavedViews } from '@/services/savedViewsService'

/**
 * Digest-page fetches as SWR hooks.
 *
 * Replaces the single tab-driven load-on-mount effect in DigestsPage — the kind
 * the react-hooks `set-state-in-effect` rule (correctly) discourages, since the
 * effect drove `subs`/`views`/`loading`/`error` synchronously off the active tab
 * and selected project. SWR now owns the loading/data/error state declaratively
 * and re-keys subscriptions on the active tab and saved views on (tab, project),
 * so the page no longer threads a `loadData` callback through an effect dep array.
 *
 * Both hooks are gated by `enabled` so each query only runs while its tab is
 * active (mirroring the old `if (tab === 'subscriptions')` / `else if` branches),
 * and each returns `mutate` so the page can refresh after a create/pause/delete
 * instead of calling the loader imperatively.
 *
 * `shouldRetryOnError: false` surfaces a failed load immediately, like the old
 * single-shot `try/catch`.
 */
const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

const SUBSCRIPTIONS_KEY = 'digest-subscriptions'
const SAVED_VIEWS_KEY = 'digest-saved-views'

export function useDigestSubscriptions(enabled: boolean) {
  const { data, error, isLoading, mutate } = useSWR<DigestSubscription[]>(
    enabled ? ([SUBSCRIPTIONS_KEY] as const) : null,
    () => listSubscriptions(),
    OPTS,
  )
  return { subscriptions: data ?? [], isLoading, isError: !!error, mutate }
}

export function useDigestSavedViews(projectId: string | undefined, enabled: boolean) {
  const { data, error, isLoading, mutate } = useSWR<SavedView[]>(
    enabled ? ([SAVED_VIEWS_KEY, projectId ?? null] as const) : null,
    ([, pid]: readonly [string, string | null]) => listSavedViews(pid ?? undefined),
    OPTS,
  )
  return { views: data ?? [], isLoading, isError: !!error, mutate }
}
