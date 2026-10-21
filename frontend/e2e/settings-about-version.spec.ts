import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test, expect, withNetRetry } from './fixtures'

/**
 * The About section shows the installed version (2026-10-21).
 *
 * No mockBackend here: the real backend answers /api/config, so this
 * test covers the whole path from `mcp_server.__version__` to the page.
 */
const here = path.dirname(fileURLToPath(import.meta.url))
const versionFile = readFileSync(path.join(here, '..', '..', 'mcp_server', '__init__.py'), 'utf-8')
const VERSION = versionFile.match(/^__version__ = "([^"]+)"$/m)![1]

test('the About section shows the version from mcp_server.__version__', async ({ page }) => {
  await withNetRetry(page, () => page.goto('/settings'))

  const about = page.locator('section', { has: page.getByRole('heading', { name: 'About' }) })
  await expect(about.getByTestId('about-version')).toHaveText(`Version ${VERSION}`)
  await expect(about.getByRole('link', { name: /View on GitHub/ })).toHaveAttribute(
    'href',
    'https://github.com/rpeck/claude-explorer',
  )
})
