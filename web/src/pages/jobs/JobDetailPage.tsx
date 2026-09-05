import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { cancelJob as postCancelJob, cancelMr as postCancelMr, fetchChat, fetchJob, fetchLogs, fetchPrompts, fetchServeLog } from '../../api/client'
import type { ChatMessage, JobItem, LogLine, PromptRow } from '../../api/types'
import { useLive } from '../../app/live'
import { ConfirmDialog } from '../../ui/ConfirmDialog'
import { LiveDot } from '../../ui/LiveDot'
import { MarkdownBody } from '../../ui/MarkdownBody'
import { MetaCard } from '../../ui/MetaCard'
import { StatusBadge } from '../../ui/StatusBadge'
import { Tabs } from '../../ui/Tabs'
import { triggerLabel } from '../../util/jobLabels'
import { useJobElapsed } from '../../util/time'
import { JobChatTab } from './JobChatTab'

function jobIsStoppable(job: JobItem | null): boolean {
  if (!job) return false
  if (job.live) return true
  const status = (job.status || '').toLowerCase()
  return status === 'queued' || status === 'running'
}

type Tab = 'overview' | 'prompt' | 'chat' | 'logs'

export function JobDetailPage() {
  const { jobId = '' } = useParams()
  const navigate = useNavigate()
  const live = useLive()
  const [job, setJob] = useState<JobItem | null>(null)
  const [prompts, setPrompts] = useState<PromptRow[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [logs, setLogs] = useState<LogLine[]>([])
  const [serveLog, setServeLog] = useState('')
  const [serveLogMissing, setServeLogMissing] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [confirm, setConfirm] = useState<'job' | 'mr' | null>(null)
  const [busy, setBusy] = useState(false)

  const seqRef = useRef(0)
  const elapsed = useJobElapsed(job)
  const canStop = jobIsStoppable(job)

  const load = useCallback(async (id: string, mine: number, opts: { clearOnError: boolean }) => {
    if (!id) return
    try {
      const body = await fetchJob(id)
      if (seqRef.current !== mine) return
      setJob(body.job)
      setError(null)
      const [p, c, l, s] = await Promise.all([
        fetchPrompts(id),
        fetchChat(id),
        fetchLogs(id),
        fetchServeLog(id),
      ])
      if (seqRef.current !== mine) return
      setPrompts(p.prompts || [])
      setMessages(c.messages || [])
      setLogs(l.lines || [])
      setServeLog(s.text || '')
      setServeLogMissing(Boolean(s.missing))
    } catch (e) {
      if (seqRef.current !== mine) return
      if (opts.clearOnError) {
        setJob(null)
        setPrompts([])
        setMessages([])
        setLogs([])
        setServeLog('')
        setServeLogMissing(true)
      }
      setError(e instanceof Error ? e.message : 'Failed to load job')
    }
  }, [])

  useEffect(() => {
    const mine = ++seqRef.current
    setTab('overview')
    setJob(null)
    setPrompts([])
    setMessages([])
    setLogs([])
    setServeLog('')
    setServeLogMissing(true)
    setError(null)
    setConfirm(null)
    setBusy(false)
    void load(jobId.trim(), mine, { clearOnError: true })
  }, [jobId, load])

  useEffect(() => {
    if (job?.live) void load(jobId.trim(), seqRef.current, { clearOnError: false })
  }, [live.generation]) // eslint-disable-line react-hooks/exhaustive-deps

  const onStopJob = async () => {
    setBusy(true)
    setError(null)
    try {
      await postCancelJob(jobId.trim())
      setConfirm(null)
      await load(jobId.trim(), seqRef.current, { clearOnError: false })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Stop failed')
    } finally {
      setBusy(false)
    }
  }

  const onStopMr = async () => {
    if (!job?.project_id || !job?.mr_iid) return
    setBusy(true)
    setError(null)
    try {
      await postCancelMr(job.project_id, job.mr_iid)
      setConfirm(null)
      navigate('/jobs')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Stop MR queue failed')
      setBusy(false)
    }
  }

  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <Link to="/jobs" className="vd-btn-ghost mb-3 inline-block text-sm">
            ← Jobs
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            {job?.jira_id && <span className="font-mono text-lg font-semibold">{job.jira_id}</span>}
            {job && <StatusBadge status={job.status} />}
            {job?.live && <LiveDot />}
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {(job?.mr_title || '').trim() || job?.job_id || 'Job'}
          </h1>
          <p className="mt-1 font-mono text-xs text-text-muted">
            {job?.job_id ? `${job.job_id}` : ''}
            {job && triggerLabel(job.trigger) ? ` · ${triggerLabel(job.trigger)}` : ''}
            {job?.agent_mode ? ` · ${job.agent_mode}` : ''}
            {job?.model ? ` · ${job.model}` : ''}
            {elapsed !== '—' ? ` · ${elapsed}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="vd-btn vd-btn-danger"
            disabled={!canStop || busy}
            onClick={() => setConfirm('job')}
          >
            Stop job
          </button>
          <button
            type="button"
            className="vd-btn vd-btn-secondary"
            disabled={!canStop || !job?.project_id || !job?.mr_iid || busy}
            onClick={() => setConfirm('mr')}
          >
            Stop MR queue
          </button>
          <button
            type="button"
            className="vd-btn vd-btn-secondary"
            disabled={busy}
            onClick={() => void load(jobId.trim(), seqRef.current, { clearOnError: true })}
          >
            Refresh
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={confirm === 'job'}
        title="Stop this job?"
        body={`Stops the OpenCode serve for this run.\n\nJob: ${job?.job_id || jobId}`}
        confirmLabel="Stop job"
        danger
        busy={busy}
        onConfirm={() => void onStopJob()}
        onCancel={() => {
          if (!busy) setConfirm(null)
        }}
      />
      <ConfirmDialog
        open={confirm === 'mr'}
        title="Stop the MR queue?"
        body={`Stops this job and every queued comment for ${job?.jira_id || 'this MR'}. The clone stays on disk.`}
        confirmLabel="Stop MR queue"
        danger
        busy={busy}
        onConfirm={() => void onStopMr()}
        onCancel={() => {
          if (!busy) setConfirm(null)
        }}
      />

      <Tabs
        tabs={[
          { id: 'overview', label: 'Details' },
          { id: 'prompt', label: 'Prompt', count: prompts.length },
          { id: 'chat', label: 'Transcript', count: messages.length },
          { id: 'logs', label: 'Logs', count: logs.length + (serveLog ? serveLog.split('\n').filter(Boolean).length : 0) },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div className="vd-panel min-h-[50vh] p-5">
        {error && <p className="text-sm text-danger-text">{error}</p>}
        {job && tab === 'overview' && <Overview job={job} elapsed={elapsed} />}
        {job && tab === 'prompt' && <PromptTab prompts={prompts} />}
        {job && tab === 'chat' && <JobChatTab messages={messages} live={job.live} />}
        {job && tab === 'logs' && (
          <LogsTab lines={logs} serveLog={serveLog} serveMissing={serveLogMissing} />
        )}
      </div>
    </section>
  )
}

function isTerminalSuccess(job: JobItem): boolean {
  return !job.live && (job.status || '').toLowerCase() === 'success'
}

function Overview({ job, elapsed }: { job: JobItem; elapsed: string }) {
  const showResult = isTerminalSuccess(job) && Boolean(job.text)
  return (
    <div className="space-y-6 text-sm">
      {showResult && (
        <div>
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-text-muted">Result</div>
          <MarkdownBody text={job.text || ''} />
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <MetaCard label="Status" valueNode={<StatusBadge status={job.status} />} />
        <MetaCard label="Trigger" mono value={triggerLabel(job.trigger) || '—'} />
        <MetaCard label="Elapsed" mono value={elapsed} />
        <MetaCard label="Title" className="sm:col-span-2" value={(job.mr_title || '').trim() || '—'} />
        <MetaCard label="Agent" value={job.agent_mode || '—'} />
        <MetaCard label="Model" mono value={job.model || '—'} />
        <MetaCard label="Session" mono value={job.session_id || '—'} />
        <MetaCard label="Branch" mono value={job.source_branch || '—'} />
        <MetaCard label="MR url" mono className="sm:col-span-2" value={job.repo_url || '—'} />
        <MetaCard label="Clone" mono className="sm:col-span-2 lg:col-span-3" value={job.clone_path || '—'} />
        <MetaCard label="Serve" mono value={job.serve_port ? `${job.serve_pid}@${job.serve_port}` : '—'} />
        <MetaCard label="Started" mono value={job.started_at || '—'} />
        <MetaCard label="Finished" mono value={job.completed_at || '—'} />
      </div>
      {job.error_message && (
        <pre className="vd-pre text-danger-text">{job.error_message}</pre>
      )}
    </div>
  )
}

function PromptTab({ prompts }: { prompts: PromptRow[] }) {
  if (prompts.length === 0) {
    return <div className="vd-alert vd-alert-warning">No prompt stored for this job.</div>
  }
  return (
    <div className="space-y-3">
      {prompts.map((p) => (
        <details key={`${p.id}-${p.posted_at}`} open className="rounded border border-border bg-bg px-3 py-2">
          <summary className="cursor-pointer font-mono text-xs">
            {p.id} · {p.posted_at}
          </summary>
          <pre className="vd-pre mt-2">{p.text}</pre>
        </details>
      ))}
    </div>
  )
}

function LogsTab({
  lines,
  serveLog,
  serveMissing,
}: {
  lines: LogLine[]
  serveLog: string
  serveMissing: boolean
}) {
  return (
    <div className="space-y-6">
      <LogBlock title="Job log" empty="No log lines for this job yet.">
        {lines.length > 0
          ? lines.map((line, i) => (
              <div key={`${line.timestamp}-${i}`} className="border-b border-border/50 py-0.5">
                {line.message}
              </div>
            ))
          : null}
      </LogBlock>
      <LogBlock
        title="OpenCode serve"
        empty={
          serveMissing
            ? 'No serve log — serve never started or the file was removed.'
            : 'Serve log is empty.'
        }
      >
        {serveLog
          ? serveLog.split('\n').map((line, i) => (
              <div key={`serve-${i}`} className="border-b border-border/50 py-0.5 whitespace-pre-wrap">
                {line || ' '}
              </div>
            ))
          : null}
      </LogBlock>
    </div>
  )
}

function LogBlock({
  title,
  empty,
  children,
}: {
  title: string
  empty: string
  children: ReactNode
}) {
  const hasBody = children != null && !(Array.isArray(children) && children.length === 0)
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-text-muted">{title}</div>
      {hasBody ? (
        <div className="max-h-[50vh] overflow-auto rounded border border-border bg-bg p-4 font-mono text-[11px] leading-relaxed text-text-secondary">
          {children}
        </div>
      ) : (
        <p className="text-sm text-text-muted">{empty}</p>
      )}
    </div>
  )
}
