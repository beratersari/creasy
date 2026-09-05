import { NavLink, Outlet } from 'react-router-dom'
import { ReportIssue } from '../ui/ReportIssue'
import { connectionLabel } from '../util/jobLabels'
import { useLive } from './live'

export function Shell() {
  const live = useLive()
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
