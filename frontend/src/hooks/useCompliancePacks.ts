import useSWR from 'swr'
import {
  compliancePackService,
  type CompliancePackRead,
} from '@/services/compliancePackService'

export function useCompliancePacks(releaseId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<CompliancePackRead[]>(
    releaseId ? ['compliance-packs', releaseId] : null,
    () => {
      if (!releaseId) {
        throw new Error('Release ID is required')
      }
      return compliancePackService.list(releaseId)
    },
    { revalidateOnFocus: false },
  )
  return {
    packs: data ?? [],
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
