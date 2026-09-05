import { describe, expect, it } from 'vitest'
import { jobMatchesFilter } from './filters'

describe('jobMatchesFilter', () => {
  it('treats running as Running and queued as Queue', () => {
    expect(jobMatchesFilter({ status: 'running', live: true }, 'active')).toBe(true)
    expect(jobMatchesFilter({ status: 'queued', live: true }, 'active')).toBe(false)
    expect(jobMatchesFilter({ status: 'queued', live: true }, 'queue')).toBe(true)
  })

  it('groups error statuses', () => {
    expect(jobMatchesFilter({ status: 'error' }, 'error')).toBe(true)
    expect(jobMatchesFilter({ status: 'timeout' }, 'error')).toBe(true)
    expect(jobMatchesFilter({ status: 'success' }, 'error')).toBe(false)
    expect(jobMatchesFilter({ status: 'success' }, 'completed')).toBe(true)
    expect(jobMatchesFilter({ status: 'cancelled' }, 'cancelled')).toBe(true)
    expect(jobMatchesFilter({ status: 'success' }, 'cancelled')).toBe(false)
  })
})
