/**
 * The About section shows the installed version (2026-10-21).
 *
 * The web page showed no version, so a user could not tell which release
 * runs, for example after `uv tool upgrade`. `/api/config` carries the
 * server's version (`mcp_server.__version__`).
 *
 * The section's "View on GitHub" link also pointed at a repository that
 * does not hold this project. It must point at rpeck/claude-explorer.
 */
import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'

import { render, screen } from '../utils'
import { server } from '../mocks/server'
import { SettingsPage } from '../../routes/SettingsPage'

function serveConfig(version?: string) {
  server.use(
    http.get('/api/config', () =>
      HttpResponse.json({
        data_dir: '/home/test/.claude-explorer/conversations',
        config_corrupt_reason: null,
        ...(version === undefined ? {} : { version }),
      }),
    ),
    http.get('/api/config/stats', () => HttpResponse.json({ conversation_count: 1 })),
  )
}

describe('SettingsPage About section', () => {
  it('shows the version that the server reports', async () => {
    serveConfig('1.2.3')
    render(<SettingsPage />)

    const about = (await screen.findByRole('heading', { name: 'About' })).closest('section')!
    expect(await screen.findByTestId('about-version')).toHaveTextContent('Version 1.2.3')
    expect(about).toContainElement(screen.getByTestId('about-version'))
  })

  it('shows no version line when an older server sends none', async () => {
    serveConfig(undefined)
    render(<SettingsPage />)

    await screen.findByText('/home/test/.claude-explorer/conversations')
    expect(screen.queryByTestId('about-version')).toBeNull()
  })

  it('links to the project repository', async () => {
    serveConfig('1.2.3')
    render(<SettingsPage />)

    const link = await screen.findByRole('link', { name: /View on GitHub/ })
    expect(link).toHaveAttribute('href', 'https://github.com/rpeck/claude-explorer')
  })
})
