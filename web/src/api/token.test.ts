import { afterEach, describe, expect, it } from 'vitest'
import { DASHBOARD_TOKEN_STORAGE_KEY, authHeaders, readDashboardToken, writeDashboardToken } from './token'

describe('dashboard token', () => {
  afterEach(() => {
    window.localStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  it('stores and reads the token for API headers', () => {
    writeDashboardToken('dash-secret')
    expect(readDashboardToken()).toBe('dash-secret')
    expect(authHeaders()).toEqual({ 'X-Creasy-Token': 'dash-secret' })
  })

  it('captures ?token= from the page URL', () => {
    window.history.replaceState({}, '', '/jobs?token=from-query')
    expect(readDashboardToken()).toBe('from-query')
    expect(window.localStorage.getItem(DASHBOARD_TOKEN_STORAGE_KEY)).toBe('from-query')
  })
})
