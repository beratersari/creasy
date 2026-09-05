export const DASHBOARD_TOKEN_STORAGE_KEY = 'creasy-dashboard-token'
export const TOKEN_EVENT = 'creasy-dashboard-token'

export function readDashboardToken(): string {
  if (typeof window === 'undefined') return ''
  try {
    const query = new URLSearchParams(window.location.search).get('token')
    if (query && query.trim()) {
      const token = query.trim()
      window.localStorage.setItem(DASHBOARD_TOKEN_STORAGE_KEY, token)
      return token
    }
    return (window.localStorage.getItem(DASHBOARD_TOKEN_STORAGE_KEY) || '').trim()
  } catch {
    return ''
  }
}

export function writeDashboardToken(token: string): void {
  const value = token.trim()
  try {
    if (value) window.localStorage.setItem(DASHBOARD_TOKEN_STORAGE_KEY, value)
    else window.localStorage.removeItem(DASHBOARD_TOKEN_STORAGE_KEY)
  } catch {
    /* ignore quota / private mode */
  }
  window.dispatchEvent(new Event(TOKEN_EVENT))
}

export function authHeaders(): Record<string, string> {
  const token = readDashboardToken()
  return token ? { 'X-Creasy-Token': token } : {}
}
