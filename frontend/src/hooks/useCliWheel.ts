import useSWR from 'swr'
import { getData } from '@/services/http'

export interface CliWheelInfo {
  available: boolean
  version: string | null
  filename: string | null
  download_url: string | null
}

/**
 * W17 DEV-02: the CLI wheel this deployment serves (public `/api/v1/sdk/cli/info`).
 * `available: false` (or an older server) keeps the guide's from-a-clone install.
 */
export function useCliWheel() {
  return useSWR<CliWheelInfo>('/api/v1/sdk/cli/info', () => getData<CliWheelInfo>('/api/v1/sdk/cli/info'), {
    revalidateOnFocus: false,
  })
}
