import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'

const login = vi.fn()

vi.mock('../../api/auth', () => ({
  login: (...args: unknown[]) => login(...args),
}))

describe('LoginPage', () => {
  afterEach(() => {
    cleanup()
    login.mockReset()
  })

  it('asks for username and password when the server has a user', async () => {
    login.mockResolvedValue(undefined)
    render(<LoginPage hasUsername />)
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'berat' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => {
      expect(login).toHaveBeenCalledWith('berat', 'secret')
    })
  })

  it('hides the username field when the server has no user', () => {
    render(<LoginPage hasUsername={false} />)
    expect(screen.queryByLabelText('Username')).toBeNull()
    expect(screen.getByLabelText('Password')).toBeTruthy()
  })

  it('shows an error when login fails', async () => {
    login.mockRejectedValue(new Error('invalid username or password'))
    render(<LoginPage hasUsername />)
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => {
      expect(screen.getByText('invalid username or password')).toBeTruthy()
    })
  })
})
