import { FormEvent, useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { ApiError, fetchJobs } from '../api/client'
import { readDashboardToken, writeDashboardToken } from '../api/token'
import { ReportIssue } from '../ui/ReportIssue'
import { connectionLabel } from '../util/jobLabels'
import { useLive } from './live'

export function Shell() {
  const live = useLive()
  const [draft, setDraft] = useState(() => readDashboardToken())
  const [needsToken, setNeedsToken] = useState(false)

  useEffect(() => {
    let gone = false
    fetchJobs({ page: 1, pageSize: 1 })
      .then(() => {
        if (!gone) setNeedsToken(false)
      })
      .catch((err) => {
        if (!gone && err instanceof ApiError && err.status === 401) setNeedsToken(true)
      })
    return () => {
      gone = true
    }
  }, [live.generation])

  function saveToken(event: FormEvent) {
    event.preventDefault()
    writeDashboardToken(draft)
    fetchJobs({ page: 1, pageSize: 1 })
      .then(() => setNeedsToken(false))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) setNeedsToken(true)
      })
  }

  return (
    <div className="vd-app">
      <aside className="vd-sidebar">
        <div className="vd-brand">
          <div className="vd-mark">CR</div>
          <div>
            <div className="text-sm font-semibold">Creasy</div>
            <div className="text-[11px] text-text-muted">
              {connectionLabel(live.connected).toLowerCase()}
              {live.running ? ` · ${live.running} running` : ''}
            </div>
          </div>
        </div>
        <nav className="vd-nav">
          <NavLink to="/jobs" className={({ isActive }) => (isActive ? 'active' : '')}>
            Jobs
          </NavLink>
        </nav>
        {needsToken ? (
          <form className="mt-3 space-y-2 px-1 text-xs" onSubmit={saveToken}>
            <label className="block text-text-muted">
              Dashboard token
              <input
                className="vd-input mt-1 w-full font-mono"
                type="password"
                autoComplete="off"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
            </label>
            <button type="submit" className="vd-btn vd-btn-secondary w-full px-3 py-1.5 text-xs">
              Save token
            </button>
          </form>
        ) : null}
        <div className="mt-3 space-y-2 px-1 text-xs">
          <ReportIssue />
        </div>
      </aside>
      <main className="vd-main">
        <div className="vd-main-inner">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
