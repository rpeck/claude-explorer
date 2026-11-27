/**
 * A2 (issue #92): the "Machines" settings (`MachinesSettings`), which the
 * sidebar's Machines button (`data-testid="machines-button"`) opens.
 *
 * The section (`data-testid="machines-settings"`, heading "Machines") sets
 * this machine's label, names other machines locally, sets the export
 * mode and rules, shows the preview, exports and imports bundles, and sets
 * or leaves the shared folder. Every write sends the header
 * `X-Claude-Explorer-Action: machines`.
 *
 * Routes: GET /api/machines, PUT /api/machines/self/label,
 * PUT /api/machines/{id}/label, GET/PUT /api/machines/export-rules,
 * GET /api/machines/export-preview, POST /api/machines/export,
 * POST /api/machines/import (multipart: file, passphrase),
 * PUT /api/machines/shared-folder, POST /api/machines/shared-folder/remove.

 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'

import { render, screen, within, waitFor } from '../utils'
import { server } from '../mocks/server'
import { MachinesSettings } from '../../components/settings/MachinesSettings'
import { MachinesButton } from '../../components/settings/MachinesButton'
import { serveApp } from '../sources/fakeRegistry'

const ME = '2f6a1b3c-0000-4000-8000-00000000000a'
const OTHER = '9d8e7f6a-0000-4000-8000-00000000000b'

interface Seen {
  method: string
  url: string
  action: string | null
  body: unknown
}

let seen: Seen[] = []
let rules: Record<string, unknown> = {}

function serve(mode: 'off' | 'export' | 'receive-only' = 'off') {
  seen = []
  rules = { version: 1, mode, allow: [{ source: 'claude-code', project: '~/Source/personal/**' }], deny: ['~/Source/work/**'], since: null }
  const record = async (request: Request, body?: unknown) => {
    seen.push({
      method: request.method,
      url: new URL(request.url).pathname,
      action: request.headers.get('X-Claude-Explorer-Action'),
      body: body === undefined ? await request.clone().json().catch(() => null) : body,
    })
  }
  server.use(
    http.get('/api/config', () =>
      HttpResponse.json({ data_dir: '/home/test/.claude-explorer/conversations', config_corrupt_reason: null }),
    ),
    http.get('/api/config/stats', () => HttpResponse.json({ conversation_count: 3 })),
    http.get('/api/machines', () =>
      HttpResponse.json({
        this_machine_id: ME,
        shared_archive_dir: '/home/test/Dropbox/ce-shared',
        machines: [
          { machine_id: ME, label: 'Home Mac', is_this_machine: true, session_count: 2 },
          { machine_id: OTHER, label: 'Work laptop', is_this_machine: false, session_count: 1 },
        ],
      }),
    ),
    http.get('/api/machines/export-rules', () => HttpResponse.json(rules)),
    http.put('/api/machines/export-rules', async ({ request }) => {
      await record(request)
      rules = (await request.json()) as Record<string, unknown>
      return HttpResponse.json(rules)
    }),
    http.get('/api/machines/export-preview', () =>
      HttpResponse.json({
        files: [
          { path: 'claude-code/projects/-home-test-Source-personal-app/s1.jsonl', bytes: 1200, source: 'claude-code', project: '/home/test/Source/personal/app' },
          { path: 'claude-code/projects/-home-test-Source-personal-app/s2.jsonl', bytes: 800, source: 'claude-code', project: '/home/test/Source/personal/app' },
        ],
        file_count: 2,
        total_bytes: 2000,
      }),
    ),
    http.put('/api/machines/self/label', async ({ request }) => {
      await record(request)
      return HttpResponse.json({ machine_id: ME, label: 'New name', is_this_machine: true, session_count: 2 })
    }),
    http.put('/api/machines/:id/label', async ({ request, params }) => {
      await record(request)
      return HttpResponse.json({ machine_id: params.id, label: 'Office', is_this_machine: false, session_count: 1 })
    }),
    http.post('/api/machines/export', async ({ request }) => {
      await record(request)
      return new HttpResponse(new Uint8Array([80, 75, 3, 4]), {
        headers: {
          'Content-Type': 'application/octet-stream',
          'Content-Disposition': 'attachment; filename="claude-explorer-Home-Mac-20261127.cebundle"',
        },
      })
    }),
    http.post('/api/machines/import', async ({ request }) => {
      // jsdom's File is not a Blob that Node's own FormData (undici) knows: the
      // file part goes out empty, and on Node 26 request.formData() throws on it.
      // So read the multipart text; a browser has no such gap.
      const text = await request.clone().text()
      const passphrase = text.match(/name="passphrase"\r\n\r\n([^\r]*)\r\n/)?.[1] ?? null
      await record(request, { file: /name="file"; filename="/.test(text) ? 'part' : null, passphrase })
      return HttpResponse.json({
        machine_id: OTHER,
        label: 'Work laptop',
        counts: { copied: 3, appended: 1, unchanged: 0, revisions: 0, skipped: 0 },
        warnings: [],
      })
    }),
    http.put('/api/machines/shared-folder', async ({ request }) => {
      await record(request)
      return HttpResponse.json({ shared_archive_dir: '/home/test/Dropbox/ce-shared' })
    }),
    http.post('/api/machines/shared-folder/remove', async ({ request }) => {
      await record(request)
      return HttpResponse.json({ removed: true })
    }),
  )
}

async function section(): Promise<HTMLElement> {
  render(<MachinesSettings />)
  const el = await screen.findByTestId('machines-settings')
  expect(within(el).getByRole('heading', { name: 'Machines' })).toBeInTheDocument()
  return el
}

function sent(url: string): Seen {
  const hit = seen.filter((s) => s.url === url)
  expect(hit, `one request to ${url}`).toHaveLength(1)
  return hit[0]
}

beforeEach(() => {
  // The page's providers ask for the preferences and the bookmarks.
  serveApp({})
  // The export button saves a download through a blob URL.
  URL.createObjectURL = vi.fn(() => 'blob:a2-test')
  URL.revokeObjectURL = vi.fn()
})

describe('the Machines section', () => {
  it('the sidebar Machines button opens it', async () => {
    serve()
    const user = userEvent.setup()
    render(<MachinesButton />)

    expect(screen.queryByTestId('machines-settings')).toBeNull()
    await user.click(screen.getByTestId('machines-button'))

    const el = await screen.findByTestId('machines-settings')
    expect(await within(el).findByTestId(`machine-row-${ME}`)).toHaveTextContent('This machine')
  })

  it('lists every machine and marks this one', async () => {
    serve()
    const el = await section()

    const mine = await within(el).findByTestId(`machine-row-${ME}`)
    const other = within(el).getByTestId(`machine-row-${OTHER}`)
    expect(mine).toHaveTextContent('This machine')
    expect(other).toHaveTextContent('Work laptop')
    expect(other).not.toHaveTextContent('This machine')
  })

  it('saves this machine label with the action header', async () => {
    serve()
    const user = userEvent.setup()
    const el = await section()

    const input = await within(el).findByTestId('machine-label-input')
    await user.clear(input)
    await user.type(input, 'New name')
    await user.click(within(el).getByTestId('machine-label-save'))

    await waitFor(() => expect(seen.some((s) => s.url === '/api/machines/self/label')).toBe(true))
    const req = sent('/api/machines/self/label')
    expect(req.method).toBe('PUT')
    expect(req.action).toBe('machines')
    expect(req.body).toEqual({ label: 'New name' })
  })

  it('renames another machine locally with its pencil', async () => {
    serve()
    const user = userEvent.setup()
    const el = await section()

    await user.click(await within(el).findByTestId(`machine-rename-${OTHER}`))
    const input = await within(el).findByTestId(`machine-rename-input-${OTHER}`)
    await user.clear(input)
    await user.type(input, 'Office{Enter}')

    await waitFor(() => expect(seen.some((s) => s.url === `/api/machines/${OTHER}/label`)).toBe(true))
    const req = sent(`/api/machines/${OTHER}/label`)
    expect(req.method).toBe('PUT')
    expect(req.action).toBe('machines')
    expect(req.body).toEqual({ label: 'Office' })
  })

  it('this machine has no rename pencil', async () => {
    serve()
    const el = await section()

    await within(el).findByTestId(`machine-row-${ME}`)
    expect(within(el).queryByTestId(`machine-rename-${ME}`)).toBeNull()
    expect(within(el).getByTestId(`machine-rename-${OTHER}`)).toBeInTheDocument()
  })
})

describe('the export mode and rules', () => {
  it('shows the three modes with their labels, off by default', async () => {
    serve('off')
    const el = await section()

    const off = await within(el).findByTestId('export-mode-off')
    await waitFor(() => expect(off).toBeChecked())
    expect(within(el).getByTestId('export-mode-export')).not.toBeChecked()
    expect(within(el).getByTestId('export-mode-receive-only')).not.toBeChecked()
    expect(el).toHaveTextContent('Off (nothing leaves this machine)')
    expect(el).toHaveTextContent('Export')
    expect(el).toHaveTextContent('Receive only')
  })

  it('shows the allow and deny rules from the server', async () => {
    serve('export')
    const el = await section()

    expect(await within(el).findByTestId('export-rules-allow')).toHaveTextContent('~/Source/personal/**')
    expect(within(el).getByTestId('export-rules-deny')).toHaveTextContent('~/Source/work/**')
    expect(within(el).getByTestId('export-rules-since')).toBeInTheDocument()
  })

  it('saves a new mode with the rules', async () => {
    serve('off')
    const user = userEvent.setup()
    const el = await section()

    await user.click(await within(el).findByTestId('export-mode-export'))
    await user.click(within(el).getByTestId('export-rules-save'))

    // A click on a mode saves at once, and Save saves again: every PUT has the new mode.
    await waitFor(() => expect(seen.some((s) => s.url === '/api/machines/export-rules')).toBe(true))
    const puts = seen.filter((s) => s.url === '/api/machines/export-rules')
    for (const req of puts) {
      expect(req.method).toBe('PUT')
      expect(req.action).toBe('machines')
      expect(req.body).toMatchObject({ mode: 'export', deny: ['~/Source/work/**'] })
    }
  })
})

describe('the preview and the bundles', () => {
  it('the preview lists the files and the total', async () => {
    serve('export')
    const el = await section()

    const preview = await within(el).findByTestId('export-preview')
    expect(preview).toHaveTextContent('s1.jsonl')
    expect(preview).toHaveTextContent('s2.jsonl')
    expect(within(el).getByTestId('export-preview-total')).toHaveTextContent(/^2 files/)
  })

  it('the export button is off unless the mode is export', async () => {
    serve('off')
    const el = await section()

    await waitFor(() => expect(within(el).getByTestId('export-mode-off')).toBeChecked())
    expect(within(el).getByTestId('export-button')).toBeDisabled()
  })

  it('export sends the passphrase, or null when it is empty', async () => {
    serve('export')
    const user = userEvent.setup()
    const el = await section()

    const button = await within(el).findByTestId('export-button')
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    await waitFor(() => expect(seen.some((s) => s.url === '/api/machines/export')).toBe(true))
    expect(sent('/api/machines/export').body).toMatchObject({ passphrase: null })
    expect(sent('/api/machines/export').action).toBe('machines')

    seen = []
    await user.type(within(el).getByTestId('export-passphrase'), 'synthetic pass')
    await user.click(button)
    await waitFor(() => expect(seen.some((s) => s.url === '/api/machines/export')).toBe(true))
    expect(sent('/api/machines/export').body).toMatchObject({ passphrase: 'synthetic pass' })
  })

  it('import uploads the file with the passphrase and shows the counts', async () => {
    serve()
    const user = userEvent.setup()
    const el = await section()

    const file = new File([new Uint8Array([80, 75, 3, 4])], 'work.cebundle', { type: 'application/octet-stream' })
    await user.upload(await within(el).findByTestId('import-file'), file)
    await user.type(within(el).getByTestId('import-passphrase'), 'synthetic pass')
    await user.click(within(el).getByTestId('import-button'))

    await waitFor(() => expect(seen.some((s) => s.url === '/api/machines/import')).toBe(true))
    const req = sent('/api/machines/import')
    expect(req.action).toBe('machines')
    // jsdom's FormData sends the File without its name or bytes, so only the part is checked.
    expect(req.body).toMatchObject({ passphrase: 'synthetic pass' })
    expect((req.body as { file: string | null }).file).not.toBeNull()
    const result = await within(el).findByTestId('import-result')
    expect(result).toHaveTextContent('3')
    expect(result).toHaveTextContent(/copied/i)
  })
})

describe('the shared folder', () => {
  it('saves the shared folder path', async () => {
    serve()
    const user = userEvent.setup()
    const el = await section()

    const input = await within(el).findByTestId('shared-folder-input')
    await user.clear(input)
    await user.type(input, '/home/test/Dropbox/ce-shared')
    await user.click(within(el).getByTestId('shared-folder-save'))

    await waitFor(() => expect(seen.some((s) => s.url === '/api/machines/shared-folder')).toBe(true))
    const req = sent('/api/machines/shared-folder')
    expect(req.method).toBe('PUT')
    expect(req.action).toBe('machines')
    expect(req.body).toEqual({ path: '/home/test/Dropbox/ce-shared' })
  })

  it('remove asks first, then sends this machine id as the confirmation', async () => {
    serve()
    const user = userEvent.setup()
    const el = await section()

    await user.click(await within(el).findByTestId('shared-folder-remove'))
    expect(seen.some((s) => s.url === '/api/machines/shared-folder/remove')).toBe(false)
    await user.click(await screen.findByTestId('shared-folder-remove-confirm'))

    await waitFor(() => expect(seen.some((s) => s.url === '/api/machines/shared-folder/remove')).toBe(true))
    const req = sent('/api/machines/shared-folder/remove')
    expect(req.action).toBe('machines')
    expect(req.body).toEqual({ confirm: ME })
  })
})
