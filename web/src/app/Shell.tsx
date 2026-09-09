import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { logout } from '../api/auth'
import { fetchMeta } from '../api/client'
import { ReportIssue } from '../ui/ReportIssue'
import { connectionLabel } from '../util/jobLabels'
import { useLive } from './live'

export function Shell({ showLogout = false }: { showLogout?: boolean }) {
  const live = useLive()
  const [version, setVersion] = useState('')

  useEffect(() => {
    let gone = false
    fetchMeta()
      .then((meta) => {
        if (!gone && meta.version) setVersion(meta.version)
      })
      .catch(() => {
        /* version is optional */
      })
    return () => {
      gone = true
    }
  }, [live.generation])

  return (
    <div className="vd-app">
      <aside className="vd-sidebar">
        <div className="vd-brand">
          <div className="vd-mark">CR</div>
          <div>
            <div className="flex flex-wrap items-baseline gap-x-2 text-sm font-semibold">
              <span>Creasy</span>
              {version ? <span className="font-mono text-xs font-medium text-text-muted">v{version}</span> : null}
            </div>
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
          <NavLink to="/settings" className={({ isActive }) => (isActive ? 'active' : '')}>
            Settings
          </NavLink>
        </nav>
        <div className="mt-3 space-y-2 px-1 text-xs">
          <ReportIssue />
          {showLogout ? (
            <button
              type="button"
              className="vd-btn vd-btn-secondary w-full px-3 py-1.5 text-xs"
              onClick={() => void logout()}
            >
              Sign out
            </button>
          ) : null}
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
