/**
 * A2 UI round 3 (issue #92): _orchestration/A2-UI.md W3.
 *
 * A click on an export-mode radio checks it at once (local state), then
 * saves. If the save fails, the radio goes back to the saved mode, and the
 * dialog shows the error.

 */
import { beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'

import { render, screen, waitFor, within } from '../utils'
import { server } from '../mocks/server'
import { MachinesSettings } from '../../components/settings/MachinesSettings'
import { serveApp } from '../sources/fakeRegistry'

const ME = '2f6a1b3c-0000-4000-8000-00000000000a'
const OTHER = '9d8e7f6a-0000-4000-8000-00000000000b'

function serve(put: () => Promise<Response>) {
  server.use(
    http.get('/api/machines', () =>
      HttpResponse.json({
        this_machine_id: ME,
        shared_archive_dir: null,
        shared_archive_block: null,
        machines: [
          { machine_id: ME, label: 'Home Mac', is_this_machine: true, session_count: 2 },
          { machine_id: OTHER, label: 'Work laptop', is_this_machine: false, session_count: 1 },
        ],
      }),
    ),
    http.get('/api/machines/export-rules', () =>
      HttpResponse.json({ version: 1, mode: 'off', allow: [], deny: [], since: null }),
    ),
    http.get('/api/machines/export-preview', () => HttpResponse.json({ files: [], file_count: 0, total_bytes: 0 })),
    http.put('/api/machines/export-rules', put),
  )
}

beforeEach(() => serveApp({}))

describe('the export mode shows a click at once (A2 UI round 3, W3)', () => {
  it('checks the radio right after the click, while the save is still open', async () => {
    let release: () => void = () => {}
    const held = new Promise<void>((r) => {
      release = r
    })
    serve(async () => {
      await held
      return HttpResponse.json({ version: 1, mode: 'export', allow: [], deny: [], since: null }) as unknown as Response
    })
    const user = userEvent.setup()
    render(<MachinesSettings />)
    const el = await screen.findByTestId('machines-settings')
    const off = await within(el).findByTestId('export-mode-off')
    await waitFor(() => expect(off).toBeChecked())

    await user.click(within(el).getByTestId('export-mode-export'))

    // The save is still open: the radio already shows the click.
    expect(within(el).getByTestId('export-mode-export')).toBeChecked()
    expect(off).not.toBeChecked()
    release()
  })

  it('goes back to the saved mode and shows the error when the save fails', async () => {
    // Held open, so that "checked at once" is checked before the failure arrives.
    let release: () => void = () => {}
    const held = new Promise<void>((r) => {
      release = r
    })
    serve(async () => {
      await held
      return HttpResponse.json({ detail: 'Synthetic save failure.' }, { status: 500 }) as unknown as Response
    })
    const user = userEvent.setup()
    render(<MachinesSettings />)
    const el = await screen.findByTestId('machines-settings')
    const off = await within(el).findByTestId('export-mode-off')
    await waitFor(() => expect(off).toBeChecked())

    await user.click(within(el).getByTestId('export-mode-export'))
    // At once: checked (local state).
    expect(within(el).getByTestId('export-mode-export')).toBeChecked()
    release()

    // After the failure: back to "off", with the error.
    await waitFor(() => expect(off).toBeChecked())
    expect(within(el).getByTestId('export-mode-export')).not.toBeChecked()
    expect(await within(el).findByText('Synthetic save failure.')).toBeInTheDocument()
  })
})
