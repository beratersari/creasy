export function triggerLabel(trigger?: string | null): string {
  const t = (trigger || '').trim().toLowerCase()
  if (t === 'review') return '/review'
  if (t === 'ask') return '/ask'
  if (t === 'reset') return '/reset'
  if (t === 'open') return 'open'
  if (t === 'update') return 'update'
  if (t === 'reopen') return 'reopen'
  return (trigger || '').trim()
}

export function connectionLabel(connected: boolean): 'Connected' | 'Reconnecting' {
  return connected ? 'Connected' : 'Reconnecting'
}
