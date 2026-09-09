import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SettingsPage } from './SettingsPage'

const fetchSettings = vi.fn()
const saveSettings = vi.fn()

vi.mock('../../api/client', () => ({
  fetchSettings: (...args: unknown[]) => fetchSettings(...args),
  saveSettings: (...args: unknown[]) => saveSettings(...args),
}))

const payload = {
  opencode_model: 'opencode/big-pickle',
  opencode_timeout: 1800,
  env_model: 'opencode/big-pickle',
  env_timeout: 1800,
  models: ['opencode/big-pickle', 'acme/fast'],
}

describe('SettingsPage', () => {
  afterEach(() => {
    cleanup()
    fetchSettings.mockReset()
    saveSettings.mockReset()
  })

  it('loads model and timeout then saves a new selection', async () => {
    fetchSettings.mockResolvedValue(payload)
    saveSettings.mockResolvedValue({
      ...payload,
      opencode_model: 'acme/fast',
      opencode_timeout: 600,
    })
    render(<SettingsPage />)
    await waitFor(() => {
      expect(screen.getByDisplayValue('opencode/big-pickle')).toBeTruthy()
    })
    fireEvent.change(screen.getByDisplayValue('opencode/big-pickle'), {
      target: { value: 'acme/fast' },
    })
    fireEvent.change(screen.getByLabelText('Timeout (seconds)'), { target: { value: '600' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      expect(saveSettings).toHaveBeenCalledWith({
        opencode_model: 'acme/fast',
        opencode_timeout: 600,
      })
    })
    expect(screen.getByText('Saved. New jobs use this model and timeout.')).toBeTruthy()
  })

  it('lets the operator type a custom provider/id', async () => {
    fetchSettings.mockResolvedValue(payload)
    saveSettings.mockResolvedValue({
      ...payload,
      opencode_model: 'anthropic/claude-sonnet-4-5',
    })
    render(<SettingsPage />)
    await waitFor(() => {
      expect(screen.getByDisplayValue('opencode/big-pickle')).toBeTruthy()
    })
    fireEvent.change(screen.getByDisplayValue('opencode/big-pickle'), {
      target: { value: '__custom__' },
    })
    fireEvent.change(screen.getByLabelText('Custom model'), {
      target: { value: 'anthropic/claude-sonnet-4-5' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      expect(saveSettings).toHaveBeenCalledWith({
        opencode_model: 'anthropic/claude-sonnet-4-5',
        opencode_timeout: 1800,
      })
    })
  })

  it('shows an error when save fails', async () => {
    fetchSettings.mockResolvedValue(payload)
    saveSettings.mockRejectedValue(new Error('model must be provider/id'))
    render(<SettingsPage />)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      expect(screen.getByText('model must be provider/id')).toBeTruthy()
    })
  })
})
