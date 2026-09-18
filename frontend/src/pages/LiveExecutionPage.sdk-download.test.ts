/** Consumer contract for the Python SDK download exposed by the live guide. */
import { describe, expect, it } from 'vitest'

import pageSource from './LiveExecutionPage.tsx?raw'

describe('LiveExecutionPage — Python SDK download contract', () => {
  it('describes the Python artifact as a ZIP', () => {
    expect(pageSource).toContain("label: 'Python (.zip)'")
    expect(pageSource).not.toContain("label: 'Python (.py)'")
  })

  it('tells users to extract and install the bundled project', () => {
    expect(pageSource).toContain('Download and extract the Python ZIP')
    expect(pageSource).toMatch(/cd python\s+pip install "\.\[yaml\]"/)
    expect(pageSource).not.toMatch(/cd python\s+pip install \.(?:\s|\\n)/)
    expect(pageSource).not.toContain('Copy the reporter from the SDK Downloads')
  })
})
