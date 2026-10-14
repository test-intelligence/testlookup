/** Consumer contract for the Python SDK download exposed by the live guide. */
import { describe, expect, it } from 'vitest'

import { getInstallSnippet } from '@/components/live/sdkSnippets'
import pageSource from './LiveExecutionPage.tsx?raw'

describe('LiveExecutionPage — Python SDK download contract', () => {
  it('describes the Python artifact as a ZIP', () => {
    expect(pageSource).toContain("label: 'Python (.zip)'")
    expect(pageSource).not.toContain("label: 'Python (.py)'")
  })

  it('tells users to extract and install the bundled project', () => {
    // The install text moved to components/live/sdkSnippets.ts (HON-03).
    const install = getInstallSnippet('python')
    expect(install).toContain('Download and extract the Python ZIP')
    expect(install).toMatch(/cd python\s+pip install "\.\[yaml\]"/)
    expect(install).not.toMatch(/cd python\s+pip install \.(?:\s|\\n)/)
    expect(install).not.toContain('Copy the reporter from the SDK Downloads')
  })
})
