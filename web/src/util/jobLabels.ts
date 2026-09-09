export function triggerLabel(trigger?: string | null): string {
  const t = (trigger || '').trim().toLowerCase()
  if (t === 'review') return 'reviewer assigned'
  if (t === 'ask') return '@mention /ask'
  if (t === 'open') return 'open'
  if (t === 'update') return 'update'
  if (t === 'reopen') return 'reopen'
  return (trigger || '').trim()
}

export function connectionLabel(connected: boolean): 'Connected' | 'Reconnecting' {
  return connected ? 'Connected' : 'Reconnecting'
}
