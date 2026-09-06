import http from 'node:http'
import type { AddressInfo } from 'node:net'
import React from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { LiveContext } from '../../app/live'
import { JobsPage } from './JobsPage'

describe('JobsPage does not refresh without the live socket', () => {
  let server: http.Server
  let origin = ''
  let realFetch: typeof fetch
  let hits = 0

  beforeEach(async () => {
    hits = 0
    server = http.createServer((req, res) => {
      if (req.method === 'OPTIONS') {
        res.writeHead(204, {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET',
        })
        res.end()
        return
      }
      const url = new URL(req.url || '/', 'http://127.0.0.1')
      if (url.pathname !== '/api/jobs') {
        res.writeHead(404, { 'Access-Control-Allow-Origin': '*' })
        res.end()
        return
      }
      hits += 1
      const jobs =
        hits === 1
          ? [{ job_id: 'job_old', jira_id: '42-7', status: 'running', live: true, mr_title: 'Old' }]
          : [{ job_id: 'job_new', jira_id: '42-7', status: 'success', live: false, mr_title: 'New' }]
      res.writeHead(200, {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
      })
      res.end(JSON.stringify({ jobs, total: 1, page: 1, page_size: 25, filter: 'all' }))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    realFetch = globalThis.fetch
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      const href = raw.startsWith('/') ? `${origin}${raw}` : raw
      return realFetch(href, init)
    }) as typeof fetch
  })

  afterEach(async () => {
    cleanup()
    globalThis.fetch = realFetch
    await new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())))
  })

  it('keeps the first page when the websocket is down and generation never ticks', async () => {
    render(
      <MemoryRouter>
        <LiveContext.Provider value={{ connected: false, generation: 0, running: 0, queueQueued: 0 }}>
          <JobsPage />
        </LiveContext.Provider>
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByText('Old')).toBeTruthy()
    })
    expect(screen.getByRole('button', { name: /refresh/i })).toBeTruthy()
    expect(screen.getByText(/list may be stale/i)).toBeTruthy()
    await new Promise((r) => setTimeout(r, 250))
    expect(hits).toBe(1)
    expect(screen.getByText('Old')).toBeTruthy()
    expect(screen.queryByText('New')).toBeNull()
    screen.getByRole('button', { name: /refresh/i }).click()
    await waitFor(() => {
      expect(screen.getByText('New')).toBeTruthy()
    })
    expect(hits).toBe(2)
  })
})
