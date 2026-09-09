import { FormEvent, useEffect, useState } from 'react'
import { fetchSettings, saveSettings } from '../../api/client'
import type { ReviewSettings } from '../../api/types'
import { PageHeader } from '../../ui/PageHeader'

const CUSTOM = '__custom__'

export function SettingsPage() {
  const [loaded, setLoaded] = useState<ReviewSettings | null>(null)
  const [model, setModel] = useState('')
  const [timeout, setTimeoutSeconds] = useState('1800')
  const [choice, setChoice] = useState(CUSTOM)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let gone = false
    fetchSettings()
      .then((data) => {
        if (gone) return
        applyPayload(data)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!gone) setError(err instanceof Error ? err.message : 'Failed to load settings')
      })
    return () => {
      gone = true
    }
  }, [])

  function applyPayload(data: ReviewSettings) {
    setLoaded(data)
    setModel(data.opencode_model)
    setTimeoutSeconds(String(data.opencode_timeout))
    setChoice(data.models.includes(data.opencode_model) ? data.opencode_model : CUSTOM)
  }

  function onPick(value: string) {
    setChoice(value)
    setSaved(null)
    if (value !== CUSTOM) setModel(value)
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setSaved(null)
    const seconds = Number(timeout)
    try {
      const data = await saveSettings({
        opencode_model: model.trim(),
        opencode_timeout: Number.isFinite(seconds) ? seconds : 0,
      })
      applyPayload(data)
      setSaved('Saved. New jobs use this model and timeout.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  async function onResetEnv() {
    if (!loaded) return
    setModel(loaded.env_model)
    setTimeoutSeconds(String(loaded.env_timeout))
    setChoice(loaded.models.includes(loaded.env_model) ? loaded.env_model : CUSTOM)
    setSaved(null)
  }

  const models = loaded?.models || []

  return (
    <section className="space-y-5">
      <PageHeader
        kicker="Review"
        title="Settings"
        description="Change the OpenCode model and turn timeout without restarting. Queued and running jobs keep the model they already have."
      />

      <form className="vd-panel max-w-xl space-y-4 p-5" onSubmit={(event) => void onSubmit(event)}>
        <label className="block text-xs text-text-muted">
          Model
          <select
            className="vd-input mt-1 font-mono"
            value={choice}
            onChange={(e) => onPick(e.target.value)}
            disabled={!loaded}
          >
            {models.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
            <option value={CUSTOM}>Custom…</option>
          </select>
        </label>

        {choice === CUSTOM ? (
          <label className="block text-xs text-text-muted">
            Custom model
            <input
              className="vd-input mt-1 font-mono"
              name="opencode_model"
              placeholder="provider/id"
              value={model}
              onChange={(e) => {
                setModel(e.target.value)
                setSaved(null)
              }}
              disabled={!loaded}
            />
          </label>
        ) : null}

        <label className="block text-xs text-text-muted">
          Timeout (seconds)
          <input
            className="vd-input mt-1 font-mono"
            name="opencode_timeout"
            type="number"
            min={1}
            max={86400}
            step={1}
            value={timeout}
            onChange={(e) => {
              setTimeoutSeconds(e.target.value)
              setSaved(null)
            }}
            disabled={!loaded}
          />
        </label>
        <p className="text-[11px] text-text-muted">
          One OpenCode turn. 1800 is 30 minutes. Range 1–86400.
        </p>

        {error ? <div className="vd-alert vd-alert-danger text-xs">{error}</div> : null}
        {saved ? <div className="vd-alert vd-alert-success text-xs">{saved}</div> : null}

        <div className="flex flex-wrap gap-2">
          <button type="submit" className="vd-btn vd-btn-primary px-4" disabled={busy || !loaded}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            className="vd-btn vd-btn-secondary px-4"
            disabled={busy || !loaded}
            onClick={() => void onResetEnv()}
          >
            Restore .env values
          </button>
        </div>
      </form>
    </section>
  )
}
