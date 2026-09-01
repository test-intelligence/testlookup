import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { OwnerCell } from './TestManagementPage'

describe('Test Management owner cell', () => {
  it('shows the trimmed source-framework owner for automation rows', () => {
    render(<OwnerCell owner="  QA Platform  " userId={null} />)

    expect(screen.getByText('QA Platform')).toBeTruthy()
    expect(screen.queryByText('Unassigned')).toBeNull()
  })

  it('falls back to the catalog assignee or author when no source owner exists', () => {
    render(<OwnerCell owner="   " userId="12345678-abcd-4000-8000-000000000001" />)

    expect(screen.getByText('12345678')).toBeTruthy()
    expect(screen.queryByText('Unassigned')).toBeNull()
  })

  it('renders Unassigned only when neither ownership source exists', () => {
    render(<OwnerCell owner={null} userId={null} />)

    expect(screen.getByLabelText('Unassigned')).toBeTruthy()
  })
})
