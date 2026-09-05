function meta(status: string): { label: string; tone: string } {
  const s = (status || '').toLowerCase()
  if (s === 'success' || s === 'completed') return { label: 'Success', tone: 'bg-success-muted text-success-text' }
  if (s === 'running') return { label: 'Running', tone: 'bg-info-muted text-info-text' }
  if (s === 'queued') return { label: 'Queued', tone: 'bg-warning-muted text-warning-text' }
  if (s === 'timeout') return { label: 'Timeout', tone: 'bg-warning-muted text-warning-text' }
  if (s === 'cancelled') return { label: 'Cancelled', tone: 'bg-surface text-text-secondary' }
  if (s === 'error') return { label: 'Error', tone: 'bg-danger-muted text-danger-text' }
  return { label: status || 'Unknown', tone: 'bg-surface text-text-secondary' }
}

export function StatusBadge({ status, size = 'md' }: { status: string; size?: 'sm' | 'md' }) {
  const m = meta(status)
  const pad = size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'
  return <span className={`vd-pill ${pad} ${m.tone}`}>{m.label}</span>
}

export function statusToneClass(status: string): string {
  const s = (status || '').toLowerCase()
  if (s === 'success') return 'tone-success'
  if (s === 'running') return 'tone-info'
  if (s === 'queued') return 'tone-warning'
  if (s === 'timeout') return 'tone-warning'
  if (s === 'cancelled') return 'tone-neutral'
  if (s === 'error') return 'tone-danger'
  return 'tone-neutral'
}
