/**
 * N8-ui: the cloud-sync gate banner and the consent dialog (plan D13, §4.3,
 * §5 "N8"; wave 4 brief, "Gate state for the banner" and "The dialog").
 *
 *   - `GET /api/config` carries `cloud_sync_gate: {provider, folder,
 *     data_dir} | null`. A persistent banner shows while it is set. It
 *     cannot be dismissed, and it opens the consent dialog.
 *   - The dialog has three steps: a warning that names the provider, a
 *     check box, and the typed provider name (case and outer spaces are
 *     ignored). Only the last step calls `POST /api/write-consent/cloud-sync`
 *     with the header `X-Claude-Explorer-Action: allow-cloud-sync-writes`
 *     and the body `{data_dir, provider}` (the trimmed typed name).
 *   - Success closes the dialog and reads the config again, so the banner
 *     goes away. An error shows the route's `detail`, and the dialog stays.
 *   - The banner mounts in RootLayout, next to ConfigCorruptionBanner.
 */
import { createElement, useState, type ComponentType, type ReactNode } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../mocks/server'
import { serveApp } from '../sources/fakeRegistry'
import { clearLocalStorage, importModule } from './codexHelpers'

const GATE = {
  provider: 'Dropbox',
  folder: '/home/u/Dropbox/claude-explorer/conversations',
  data_dir: '/home/u/Dropbox/claude-explorer/conversations',
}

const CONSENT_URL = '/api/write-consent/cloud-sync'

interface ConsentCall {
  action: string | null
  contentType: string | null
  body: unknown
}

/** Serve /api/config with the gate until a consent POST succeeds. */
function serveGate(consent: { status: number; detail?: string } = { status: 200 }) {
  const calls: ConsentCall[] = []
  let gate: typeof GATE | null = GATE
  server.use(
    http.get('/api/config', () =>
      HttpResponse.json({ data_dir: GATE.data_dir, config_corrupt_reason: null, cloud_sync_gate: gate }),
    ),
    http.post(CONSENT_URL, async ({ request }) => {
      calls.push({
        action: request.headers.get('X-Claude-Explorer-Action'),
        contentType: request.headers.get('Content-Type'),
        body: await request.json(),
      })
      if (consent.status !== 200) {
        return HttpResponse.json({ detail: consent.detail }, { status: consent.status })
      }
      gate = null
      return HttpResponse.json({ allow_writes_in_cloud_sync: GATE.data_dir })
    }),
  )
  return calls
}

function withQueryClient(node: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } })
  return createElement(QueryClientProvider, { client: queryClient }, node)
}

type DialogProps = { open: boolean; onOpenChange: (open: boolean) => void; gate: typeof GATE }

async function loadDialog() {
  return (
    await importModule<{ CloudSyncConsentDialog: ComponentType<DialogProps> }>(
      '@/components/settings/CloudSyncConsentDialog',
    )
  ).CloudSyncConsentDialog
}

async function loadBanner() {
  return (await importModule<{ CloudSyncGateBanner: ComponentType }>('@/components/layout/CloudSyncGateBanner'))
    .CloudSyncGateBanner
}

/** The dialog behind a button, so a test can close and open it again. */
async function renderDialog() {
  const Dialog = await loadDialog()
  const onOpenChange = vi.fn()
  function Host() {
    const [open, setOpen] = useState(true)
    return createElement(
      'div',
      null,
      createElement('button', { type: 'button', onClick: () => setOpen(true) }, 'Open consent'),
      createElement(Dialog, {
        open,
        gate: GATE,
        onOpenChange: (next: boolean) => {
          onOpenChange(next)
          setOpen(next)
        },
      }),
    )
  }
  render(withQueryClient(createElement(Host)))
  const dialog = await screen.findByRole('dialog')
  return { dialog, onOpenChange }
}

const user = () => userEvent.setup()

async function toStepTwo(u: ReturnType<typeof user>, dialog: HTMLElement) {
  await u.click(within(dialog).getByRole('button', { name: /continue/i }))
  await within(dialog).findByRole('checkbox')
}

async function toStepThree(u: ReturnType<typeof user>, dialog: HTMLElement) {
  await toStepTwo(u, dialog)
  await u.click(within(dialog).getByRole('checkbox'))
  await u.click(within(dialog).getByRole('button', { name: /continue/i }))
  await within(dialog).findByRole('textbox')
}

const allowButton = (dialog: HTMLElement) => within(dialog).getByRole('button', { name: /allow writes/i })

beforeEach(clearLocalStorage)

describe('CloudSyncGateBanner', () => {
  it('renders nothing while the gate is open (cloud_sync_gate is null)', async () => {
    let served = false
    server.use(
      http.get('/api/config', () => {
        served = true
        return HttpResponse.json({ data_dir: '/d', config_corrupt_reason: null, cloud_sync_gate: null })
      }),
    )
    const Banner = await loadBanner()
    render(withQueryClient(createElement(Banner)))
    await waitFor(() => expect(served).toBe(true))
    expect(screen.queryByTestId('cloud-sync-gate-banner')).toBeNull()
  })

  it('renders nothing for an older server without the field', async () => {
    let served = false
    server.use(
      http.get('/api/config', () => {
        served = true
        return HttpResponse.json({ data_dir: '/d', config_corrupt_reason: null })
      }),
    )
    const Banner = await loadBanner()
    render(withQueryClient(createElement(Banner)))
    await waitFor(() => expect(served).toBe(true))
    expect(screen.queryByTestId('cloud-sync-gate-banner')).toBeNull()
  })

  it('while the gate is closed, an alert names the provider and the folder', async () => {
    serveGate()
    const Banner = await loadBanner()
    render(withQueryClient(createElement(Banner)))
    const banner = await screen.findByTestId('cloud-sync-gate-banner')
    expect(banner).toHaveAttribute('role', 'alert')
    expect(banner).toHaveTextContent(GATE.provider)
    expect(banner).toHaveTextContent(GATE.folder)
  })

  it('the banner cannot be dismissed', async () => {
    serveGate()
    const Banner = await loadBanner()
    render(withQueryClient(createElement(Banner)))
    const banner = await screen.findByTestId('cloud-sync-gate-banner')
    expect(within(banner).queryByRole('button', { name: /dismiss|close|hide/i })).toBeNull()
    const buttons = within(banner).getAllByRole('button')
    expect(buttons).toHaveLength(1)
    expect(buttons[0]).toHaveAccessibleName(/allow writes/i)
  })

  it('the banner button opens the consent dialog, which names the provider', async () => {
    serveGate()
    const Banner = await loadBanner()
    render(withQueryClient(createElement(Banner)))
    const banner = await screen.findByTestId('cloud-sync-gate-banner')
    expect(screen.queryByRole('dialog')).toBeNull()
    await user().click(within(banner).getByRole('button', { name: /allow writes/i }))
    expect(await screen.findByRole('dialog')).toHaveTextContent(GATE.provider)
  })

  it('after the consent succeeds, the config is read again and the banner goes away', async () => {
    const calls = serveGate()
    const Banner = await loadBanner()
    render(withQueryClient(createElement(Banner)))
    const banner = await screen.findByTestId('cloud-sync-gate-banner')
    const u = user()
    await u.click(within(banner).getByRole('button', { name: /allow writes/i }))
    const dialog = await screen.findByRole('dialog')
    await toStepThree(u, dialog)
    await u.type(within(dialog).getByRole('textbox'), GATE.provider)
    await u.click(allowButton(dialog))
    await waitFor(() => expect(screen.queryByTestId('cloud-sync-gate-banner')).toBeNull())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(calls).toHaveLength(1)
  })
})

describe('RootLayout mounts the banner', () => {
  it('with a closed gate, the app shell shows the banner', async () => {
    serveApp({})
    serveGate()
    server.use(
      http.get('/api/health/watcher', () =>
        HttpResponse.json({ installed: true, platform: 'darwin', install_command: '', docs_url: '' }),
      ),
    )
    const { AppProviders } = await importModule<{ AppProviders: ComponentType<{ children: ReactNode }> }>(
      '@/test/sources/harness',
    )
    const { RootLayout } = await importModule<{ RootLayout: ComponentType }>('@/components/layout/RootLayout')
    render(createElement(AppProviders, null, createElement(RootLayout)))
    expect(await screen.findByTestId('cloud-sync-gate-banner')).toHaveTextContent(GATE.provider)
  })
})

describe('CloudSyncConsentDialog: three steps', () => {
  it('step 1 names the provider and the folder, and has no check box or text field yet', async () => {
    serveGate()
    const { dialog } = await renderDialog()
    expect(dialog).toHaveTextContent(GATE.provider)
    expect(dialog).toHaveTextContent(GATE.folder)
    expect(within(dialog).queryByRole('checkbox')).toBeNull()
    expect(within(dialog).queryByRole('textbox')).toBeNull()
  })

  it('step 2: Continue stays disabled until the check box is checked', async () => {
    serveGate()
    const { dialog } = await renderDialog()
    const u = user()
    await toStepTwo(u, dialog)
    const next = within(dialog).getByRole('button', { name: /continue/i })
    expect(next).toBeDisabled()
    await u.click(within(dialog).getByRole('checkbox'))
    expect(next).toBeEnabled()
  })

  it('step 3: Allow writes stays disabled until the typed name matches the provider', async () => {
    serveGate()
    const { dialog } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    expect(allowButton(dialog)).toBeDisabled()
    await u.type(within(dialog).getByRole('textbox'), 'Dropbo')
    expect(allowButton(dialog)).toBeDisabled()
    await u.type(within(dialog).getByRole('textbox'), 'x')
    expect(allowButton(dialog)).toBeEnabled()
  })

  it('a different name of the same length does not match', async () => {
    serveGate()
    const { dialog } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    await u.type(within(dialog).getByRole('textbox'), 'OneDriv')
    expect(allowButton(dialog)).toBeDisabled()
  })

  it('the typed name ignores case and outer spaces, and the POST sends it trimmed', async () => {
    const calls = serveGate()
    const { dialog } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    await u.type(within(dialog).getByRole('textbox'), '  dropbox  ')
    expect(allowButton(dialog)).toBeEnabled()
    await u.click(allowButton(dialog))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].body).toEqual({ data_dir: GATE.data_dir, provider: 'dropbox' })
  })
})

describe('CloudSyncConsentDialog: the consent call', () => {
  it('no request is sent before the last click', async () => {
    const calls = serveGate()
    const { dialog } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    await u.type(within(dialog).getByRole('textbox'), GATE.provider)
    expect(calls).toHaveLength(0)
  })

  it('the last click sends one POST with the action header and {data_dir, provider}', async () => {
    const calls = serveGate()
    const { dialog } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    await u.type(within(dialog).getByRole('textbox'), GATE.provider)
    await u.click(allowButton(dialog))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].action).toBe('allow-cloud-sync-writes')
    expect(calls[0].contentType).toMatch(/application\/json/)
    expect(calls[0].body).toEqual({ data_dir: GATE.data_dir, provider: GATE.provider })
  })

  it('success closes the dialog', async () => {
    serveGate()
    const { dialog, onOpenChange } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    await u.type(within(dialog).getByRole('textbox'), GATE.provider)
    await u.click(allowButton(dialog))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  for (const [status, detailText] of [
    [409, 'The data folder is now /elsewhere, synced by iCloud Drive (/elsewhere). Open the dialog again.'],
    [503, 'Config file is corrupt; refuse to write.'],
  ] as const) {
    it(`an HTTP ${status} shows the route's detail, and the dialog stays open`, async () => {
      const calls = serveGate({ status, detail: detailText })
      const { dialog, onOpenChange } = await renderDialog()
      const u = user()
      await toStepThree(u, dialog)
      await u.type(within(dialog).getByRole('textbox'), GATE.provider)
      await u.click(allowButton(dialog))
      await waitFor(() => expect(calls).toHaveLength(1))
      expect(await within(dialog).findByText(detailText, { exact: false })).toBeInTheDocument()
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(onOpenChange).not.toHaveBeenCalledWith(false)
    })
  }

  it('Cancel in step 3 sends nothing and closes the dialog', async () => {
    const calls = serveGate()
    const { dialog, onOpenChange } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    await u.type(within(dialog).getByRole('textbox'), GATE.provider)
    await u.click(within(dialog).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(calls).toHaveLength(0)
  })

  it('the dialog opened again starts at step 1', async () => {
    serveGate()
    const { dialog } = await renderDialog()
    const u = user()
    await toStepThree(u, dialog)
    await u.click(within(dialog).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await u.click(screen.getByRole('button', { name: 'Open consent' }))
    const again = await screen.findByRole('dialog')
    expect(within(again).queryByRole('checkbox')).toBeNull()
    expect(within(again).queryByRole('textbox')).toBeNull()
    expect(within(again).getByRole('button', { name: /continue/i })).toBeEnabled()
  })
})
