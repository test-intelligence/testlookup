import useSWR from 'swr'
import {
  compliancePackService,
  type CompliancePackRead,
} from '@/services/compliancePackService'

export function useCompliancePacks(releaseId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<CompliancePackRead[]>(
    releaseId ? ['compliance-packs', releaseId] : null,
    () => compliancePackService.list(releaseId!),
    { revalidateOnFocus: false },
  )
  return {
    packs: data ?? [],
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
