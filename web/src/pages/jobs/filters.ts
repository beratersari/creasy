export const JOB_FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'active', label: 'Running' },
  { id: 'queue', label: 'Queue' },
  { id: 'error', label: 'Error' },
  { id: 'completed', label: 'Success' },
  { id: 'cancelled', label: 'Cancelled' },
] as const

export type JobListFilter = (typeof JOB_FILTERS)[number]['id']

export function jobMatchesFilter(
  job: { status?: string; live?: boolean },
  filter: JobListFilter,
): boolean {
  const s = (job.status || '').toLowerCase()
  if (filter === 'all') return true
  if (filter === 'active') return s === 'running' || Boolean(job.live && s !== 'queued')
  if (filter === 'queue') return s === 'queued'
  if (filter === 'error') return s === 'error' || s === 'timeout'
  if (filter === 'completed') return s === 'success'
  if (filter === 'cancelled') return s === 'cancelled'
  return true
}

export function emptyJobDetail() {
  return {
    job: null as null,
    prompts: [] as unknown[],
    messages: [] as unknown[],
    logs: [] as unknown[],
    error: null as string | null,
  }
}
