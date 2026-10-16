/**
 * TRND-01: whether the Trends email can be delivered, asked ONLY while the
 * Email report dialog is open (no page-load request, `rollout-trends`'
 * exact inventory). The answer goes through `parseEmailAvailability`: a
 * malformed one is "not available", never a crash.
 */
import useSWR from 'swr'
import { getEmailAvailability } from '@/services/trendsReportService'
import { parseEmailAvailability, type EmailAvailability } from '@/types/trends'

export const EMAIL_AVAILABILITY_KEY = 'email-trends-availability'

export function useEmailAvailability(open: boolean): {
  availability: EmailAvailability | null
  isLoading: boolean
  error: unknown
} {
  const { data, error, isLoading } = useSWR(open ? EMAIL_AVAILABILITY_KEY : null, () => getEmailAvailability(), {
    revalidateOnFocus: false,
  })
  return { availability: parseEmailAvailability(data), isLoading: Boolean(isLoading), error }
}
