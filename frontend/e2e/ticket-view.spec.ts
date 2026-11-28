import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'
import type { Page, Route } from '@playwright/test'
import type { ConversationDetail, ConversationSummary } from '../src/lib/types'


/** True when {title} or {cwd} is between POSIX quotes in a template. */
function placeholderInQuotes(s: string): boolean {
  let quote: string | null = null
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (c === '\\' && quote !== "'") {
      i++
      continue
    }
    if (c === "'" || c === '"') quote = quote === null ? c : quote === c ? null : quote
    else if (quote !== null && (s.startsWith('{title}', i) || s.startsWith('{cwd}', i))) return true
  }
  return false
}

/**
 * T1 (issue #93): the ticket page and the start command. The contract
 * (orchestrator, 2026-11-27):
 *
 *   - Route /tickets/:owner/:repo/:number (owner and repo "_" for the unknown
 *     repo). It reads GET /api/tickets/<owner>/<repo>/<number>.
 *   - "ticket-heading": "owner/repo#84", or "#84 (unknown repo)".
 *   - One "ticket-session" per session: "ticket-session-link" to
 *     /conversations/<uuid>, the name, the "entrypoint-chip", the state, and
 *     the machine label. Each "ticket-ref" shows its snippet and links to
 *     ?m=<message_uuid> when it has one.
 *   - "ticket-pr": "owner/repo#85", href = the PR url, in a new tab.
 *   - "ticket-empty": "No sessions refer to this ticket."
 *   - On 503, "ticket-not-ready": "The ticket index is not ready yet."
 *   - "start-session-button" ("Copy start command") only when the preference
 *     "start_session_command" is not empty. A click copies the filled command
 *     ({title} = the newest session's name, {cwd} = the newest project_path),
 *     shows "Command copied", and sends nothing to the backend.
 *   - Settings: section data-section "start-session", input
 *     "start-session-command-input" (label "Start session command"); it lists
 *     the placeholders {repo}, {issue}, {title}, {cwd}.
 *   - Route /tickets/key/:key (Linear, Jira) with the same testids; heading
 *     "ENG-123"; "ticket-external-link" ("Open in Linear" or "Open in Jira",
 *     href = url) only when the ticket has a url.
 *   - The ticket page lists every message that names the ticket, each with
 *     its ?m= link.
 *   - The header "ticket-link" opens the ticket page.
 *   - The atom editor target select "filter-editor-target" (a native select,
 *     option value = the FilterTarget id) offers "Ticket" and "Entrypoint";
 *     its default is 'title'. A saved atom with either target filters the list.
 *
 * mockBackend serves the payloads below. The console gate of TESTING.md
 * §5.15 runs for every test.
 */

const S1 = 'a9300000-0000-4000-8000-000000000001'
const S5 = 'a9300000-0000-4000-8000-000000000005'
const MACHINE = '0e000000-0000-4000-8000-0000000000e0'
const PR_URL = 'https://github.com/fixture-org/alpha/pull/85'
const TITLES: Record<string, string> = {
  [S1]: 'Work on fixture-org/alpha#84',
  [S5]: 'The laptop session',
}

function summary(uuid: string, fields: Record<string, unknown> = {}): ConversationSummary {
  return makeSummary({
    uuid,
    name: TITLES[uuid],
    source: 'CLAUDE_CODE',
    message_count: 2,
    human_message_count: 1,
    project_path: '/home/fixture/alpha',
    project_name: 'alpha',
    ...fields,
  } as unknown as Partial<ConversationSummary> & { uuid: string })
}

const details: Record<string, ConversationDetail> = {
  [S1]: makeDetail(
    summary(S1),
    [
      makeMessage({ uuid: 's1-prompt', sender: 'human', text: 'Please fix #84 now.', content: [{ type: 'text', text: 'Please fix #84 now.' }] }),
      makeMessage({ uuid: 's1-reply', sender: 'assistant', text: 'Fixed.', parent_message_uuid: 's1-prompt', content: [{ type: 'text', text: 'Fixed.' }] }),
    ],
    {
      entrypoint: 'cli', tickets: ['fixture-org/alpha#84', 'fixture-org/alpha#85'],
      named_keys: ['fixture-org/alpha#84'], pr_keys: ['fixture-org/alpha#85'],
    } as unknown as Partial<ConversationDetail>,
  ),
}

function session(uuid: string, fields: Record<string, unknown>) {
  return {
    conversation_uuid: uuid,
    name: TITLES[uuid],
    source: 'CLAUDE_CODE',
    project_path: '/home/fixture/alpha',
    entrypoint: 'cli',
    machine_id: null,
    machine_label: null,
    state: 'ended',
    created_at: '2026-11-20T09:01:00Z',
    updated_at: '2026-11-20T09:01:06Z',
    kinds: ['message'],
    refs: [],
    ...fields,
  }
}

const ALPHA_84 = {
  key: 'fixture-org/alpha#84',
  repo: 'fixture-org/alpha',
  number: 84,
  system: 'github',
  url: 'https://github.com/fixture-org/alpha/issues/84',
  sessions: [
    session(S5, {
      project_path: '/home/fixture/laptop',
      entrypoint: 'claude-desktop',
      machine_id: MACHINE,
      machine_label: 'Fixture laptop',
      updated_at: '2026-11-20T09:05:02Z',
      refs: [{ kind: 'message', message_uuid: 's5-prompt', snippet: 'fixture-org/alpha#84 from the laptop.' }],
    }),
    session(S1, {
      kinds: ['branch', 'message', 'title'],
      refs: [
        { kind: 'title', message_uuid: null, snippet: TITLES[S1] },
        { kind: 'message', message_uuid: 's1-prompt', snippet: 'Please fix #84 now.' },
        { kind: 'branch', message_uuid: null, snippet: 'fix/84-search-tools' },
      ],
    }),
  ],
  prs: [{ number: 85, repository: 'fixture-org/alpha', url: PR_URL, conversation_uuid: S1, timestamp: '2026-11-20T09:01:04Z' }],
}

type TicketReply = { status: number; body: unknown }

async function serve(page: Page, mockBackend: (o?: Record<string, unknown>) => Promise<void>, opts: {
  tickets?: Record<string, TicketReply>
  preferences?: Record<string, unknown>
  sharedPrefsState?: { data: Record<string, unknown> }
} = {}): Promise<string[]> {
  const sent: string[] = []
  const tickets = opts.tickets ?? { 'fixture-org/alpha/84': { status: 200, body: ALPHA_84 } }
  page.on('request', (req) => {
    if (req.url().includes('/api/')) sent.push(`${req.method()} ${req.url()} ${req.postData() ?? ''}`)
  })
  await mockBackend({
    conversations: [summary(S1, { entrypoint: 'cli', tickets: ['fixture-org/alpha#84'] })],
    details,
    preferences: opts.preferences,
    sharedPrefsState: opts.sharedPrefsState,
    extraRoutes: async (p: Page) => {
      await p.route('**/api/tickets/**', async (route: Route) => {
        const path = new URL(route.request().url()).pathname.replace(/^\/api\/tickets\//, '')
        const reply = tickets[path] ?? {
          status: 200,
          body: { key: 'x', repo: null, number: 0, sessions: [], prs: [] },
        }
        await route.fulfill({ status: reply.status, contentType: 'application/json', body: JSON.stringify(reply.body) })
      })
    },
  })
  return sent
}

async function openTicket(page: Page, path: string) {
  await withNetRetry(page, () => page.goto(`/tickets/${path}`))
}

async function stubClipboard(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { __copied: string[] }
    w.__copied = []
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text: string) => { w.__copied.push(text) } },
    })
  })
}

const copied = (page: Page) => page.evaluate(() => (window as unknown as { __copied: string[] }).__copied)

test.describe('Ticket view (T1)', () => {
  test('the ticket page lists the sessions, newest first, with chip, machine, and state', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/84')
    await expect(page.getByTestId('ticket-heading')).toHaveText('fixture-org/alpha#84')
    const rows = page.getByTestId('ticket-session')
    await expect(rows).toHaveCount(2)
    await expect(rows.nth(0).getByTestId('ticket-session-link')).toHaveText(TITLES[S5])
    await expect(rows.nth(0).getByTestId('ticket-session-link')).toHaveAttribute('href', `/conversations/${S5}`)
    await expect(rows.nth(0).getByTestId('entrypoint-chip')).toHaveText('Desktop')
    await expect(rows.nth(0)).toContainText('Fixture laptop')
    await expect(rows.nth(1).getByTestId('entrypoint-chip')).toHaveText('CLI')
    await expect(rows.nth(1)).toContainText('ended')
  })

  test('a message ref links to its message; a title ref has no link', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/84')
    const refs = page.getByTestId('ticket-session').nth(1).getByTestId('ticket-ref')
    await expect(refs).toHaveCount(3)
    const msg = refs.filter({ hasText: 'Please fix #84 now.' })
    await expect(msg.getByRole('link')).toHaveAttribute('href', `/conversations/${S1}?m=s1-prompt`)
    await expect(refs.filter({ hasText: 'fix/84-search-tools' }).getByRole('link')).toHaveCount(0)
    await msg.getByRole('link').click()
    await expect(page).toHaveURL(new RegExp(`/conversations/${S1}\\?m=s1-prompt$`))
    await expect(page.locator('header').first().getByRole('heading', { level: 1, name: TITLES[S1] })).toBeVisible()
  })

  test('the PR links open the pull request in a new tab', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/84')
    const pr = page.getByTestId('ticket-pr')
    await expect(pr).toHaveText('fixture-org/alpha#85')
    const link = page.getByRole('link', { name: 'fixture-org/alpha#85' })
    await expect(link).toHaveAttribute('href', PR_URL)
    await expect(link).toHaveAttribute('target', '_blank')
  })

  test('every message that names the ticket is listed with its link', async ({ page, mockBackend }) => {
    const two = {
      ...ALPHA_84,
      sessions: [
        session(S1, {
          refs: [
            { kind: 'message', message_uuid: 's1-prompt', snippet: 'Please fix #84 now.' },
            { kind: 'message', message_uuid: 's1-reply', snippet: 'Done with fixture-org/alpha#84.' },
          ],
        }),
      ],
    }
    await serve(page, mockBackend, { tickets: { 'fixture-org/alpha/84': { status: 200, body: two } } })
    await openTicket(page, 'fixture-org/alpha/84')
    const hrefs = page.getByTestId('ticket-ref').getByRole('link')
    await expect(hrefs).toHaveCount(2)
    await expect(hrefs.nth(0)).toHaveAttribute('href', `/conversations/${S1}?m=s1-prompt`)
    await expect(hrefs.nth(1)).toHaveAttribute('href', `/conversations/${S1}?m=s1-reply`)
  })

  test('a Linear ticket page names the key and links to Linear', async ({ page, mockBackend }) => {
    const body = { ...ALPHA_84, key: 'ENG-123', repo: null, number: 123, system: 'linear', url: 'https://linear.app/acme/issue/ENG-123', prs: [] }
    await serve(page, mockBackend, { tickets: { 'key/ENG-123': { status: 200, body } } })
    await openTicket(page, 'key/ENG-123')
    await expect(page.getByTestId('ticket-heading')).toHaveText('ENG-123')
    await expect(page.getByTestId('ticket-session')).toHaveCount(2)
    const ext = page.getByTestId('ticket-external-link')
    await expect(ext).toHaveText('Open in Linear (acme)')
    await expect(ext).toHaveAttribute('href', 'https://linear.app/acme/issue/ENG-123')
  })

  test('a Jira ticket page links to Jira only when a URL is known', async ({ page, mockBackend }) => {
    const jira = (key: string, url: string | null) =>
      ({ ...ALPHA_84, key, repo: null, number: Number(key.split('-')[1]), system: 'jira', url, prs: [] })
    await serve(page, mockBackend, {
      tickets: {
        'key/DATA-4': { status: 200, body: jira('DATA-4', 'https://fixture.atlassian.net/browse/DATA-4') },
        'key/PROJ-7': { status: 200, body: jira('PROJ-7', null) },
      },
    })
    await openTicket(page, 'key/DATA-4')
    await expect(page.getByTestId('ticket-external-link')).toHaveText('Open in Jira (fixture.atlassian.net)')
    await expect(page.getByTestId('ticket-external-link')).toHaveAttribute('href', 'https://fixture.atlassian.net/browse/DATA-4')
    await openTicket(page, 'key/PROJ-7')
    await expect(page.getByTestId('ticket-heading')).toHaveText('PROJ-7')
    await expect(page.getByTestId('ticket-external-link')).toHaveCount(0)
  })

  test('the unknown-repo ticket names itself so', async ({ page, mockBackend }) => {
    await serve(page, mockBackend, {
      tickets: { '_/_/84': { status: 200, body: { ...ALPHA_84, key: '#84', repo: null, prs: [] } } },
    })
    await openTicket(page, '_/_/84')
    await expect(page.getByTestId('ticket-heading')).toHaveText('#84 (unknown repo)')
  })

  test('a ticket without sessions says so', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/9999')
    await expect(page.getByTestId('ticket-empty')).toHaveText('No sessions refer to this ticket.')
    await expect(page.getByTestId('ticket-session')).toHaveCount(0)
  })

  test('a ticket page while the index builds says so', async ({ page, mockBackend, consoleAssertions }) => {
    // The browser logs the 503 that the page expects.
    consoleAssertions.allowlist.push(/status of 503/)
    await serve(page, mockBackend, {
      tickets: { 'fixture-org/alpha/84': { status: 503, body: { detail: 'ticket index not ready' } } },
    })
    await openTicket(page, 'fixture-org/alpha/84')
    await expect(page.getByTestId('ticket-not-ready')).toHaveText('The ticket index is not ready yet.')
  })

  test('the header ticket link opens the ticket page', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await withNetRetry(page, () => page.goto(`/conversations/${S1}`))
    const link = page.locator('header').first().getByTestId('ticket-link').first()
    await expect(link).toHaveText('fixture-org/alpha#84')
    await link.click()
    await expect(page).toHaveURL(/\/tickets\/fixture-org\/alpha\/84$/)
    await expect(page.getByTestId('ticket-heading')).toHaveText('fixture-org/alpha#84')
  })

  test('without a start command there is no start button', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/84')
    await expect(page.getByTestId('ticket-session')).toHaveCount(2)
    await expect(page.getByTestId('start-session-button')).toHaveCount(0)
  })

  test('the start button copies the filled command and sends nothing', async ({ page, mockBackend }) => {
    await stubClipboard(page)
    const sent = await serve(page, mockBackend, {
      preferences: { start_session_command: 'claude --bg "Fix {repo}#{issue}: "{title} --cwd {cwd}' },
    })
    await openTicket(page, 'fixture-org/alpha/84')
    const button = page.getByTestId('start-session-button')
    await expect(button).toHaveText('Copy start command')
    const before = sent.length
    await button.click()
    await expect(page.getByText('Command copied')).toBeVisible()
    expect(await copied(page)).toEqual([
      "claude --bg \"Fix fixture-org/alpha#84: \"'The laptop session' --cwd '/home/fixture/laptop'",
    ])
    expect(sent.slice(before).filter((r) => !r.startsWith('GET '))).toEqual([])
    expect(sent.filter((r) => r.includes('claude --bg') && !r.includes('/api/preferences'))).toEqual([])
  })

  for (const template of ['claude --bg "Fix {title}"', "cd '{cwd}' && claude"]) {
    test(`a template with a quoted placeholder warns and shows no button: ${template}`, async ({ page, mockBackend }) => {
      await serve(page, mockBackend, { preferences: { start_session_command: template } })
      await withNetRetry(page, () => page.goto('/settings'))
      await expect(page.locator('[data-section="start-session"]')).toContainText(
        'Put {title} and {cwd} outside quotes: Explorer quotes them.',
      )
      await openTicket(page, 'fixture-org/alpha/84')
      await expect(page.getByTestId('ticket-session')).toHaveCount(2)
      await expect(page.getByTestId('start-session-button')).toHaveCount(0)
    })
  }

  test('a template with the placeholders outside quotes has no warning', async ({ page, mockBackend }) => {
    await serve(page, mockBackend, { preferences: { start_session_command: 'claude --bg "Fix {repo}#{issue}: "{title}' } })
    await withNetRetry(page, () => page.goto('/settings'))
    await expect(page.getByTestId('start-session-command-input')).toHaveValue('claude --bg "Fix {repo}#{issue}: "{title}')
    await expect(page.getByText('Put {title} and {cwd} outside quotes: Explorer quotes them.')).toHaveCount(0)
  })

  test('every example in the Settings help puts {title} and {cwd} outside quotes', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await withNetRetry(page, () => page.goto('/settings'))
    const section = page.locator('[data-section="start-session"]')
    await expect(section).toBeVisible()
    const examples = [
      ...(await section.locator('code').allTextContents()),
      (await page.getByTestId('start-session-command-input').getAttribute('placeholder')) ?? '',
    ].filter((s) => s.includes('{title}') || s.includes('{cwd}'))
    // At least one example shows how to place {title}: else this test proves nothing.
    expect(examples.filter((s) => s.includes('{title}') && s.length > '{title}'.length)).not.toEqual([])
    expect(examples.filter(placeholderInQuotes)).toEqual([])
  })

  test('the settings page saves the start command, and the button appears', async ({ page, mockBackend }) => {
    const prefs = { data: {} as Record<string, unknown> }
    await serve(page, mockBackend, { sharedPrefsState: prefs })
    await withNetRetry(page, () => page.goto('/settings'))
    const section = page.locator('[data-section="start-session"]')
    await expect(section).toContainText('{repo}')
    await expect(section).toContainText('{issue}')
    await expect(section).toContainText('{title}')
    await expect(section).toContainText('{cwd}')
    const input = page.getByTestId('start-session-command-input')
    await expect(input).toHaveValue('')
    await expect(page.getByLabel('Start session command')).toBeVisible()
    await input.fill('claude --cloud "Fix {repo}#{issue}"')
    await input.blur()
    await expect.poll(() => prefs.data.start_session_command).toBe('claude --cloud "Fix {repo}#{issue}"')
    await openTicket(page, 'fixture-org/alpha/84')
    await expect(page.getByTestId('start-session-button')).toBeVisible()
  })
})

test.describe('Ticket and entrypoint filter targets (T1)', () => {
  const rows = [
    makeSummary({ uuid: 'c-alpha', name: 'Alpha session', tickets: ['fixture-org/alpha#84'], entrypoint: 'cli' } as unknown as Partial<ConversationSummary> & { uuid: string }),
    makeSummary({ uuid: 'c-beta', name: 'Beta session', tickets: ['fixture-org/beta#7'], entrypoint: 'sdk-cli' } as unknown as Partial<ConversationSummary> & { uuid: string }),
  ]

  function seed(target: string, patterns: string[]) {
    return {
      filters: {
        nodes: {
          'a-1': { id: 'a-1', type: 'atom', name: 'By target', enabled: true, behavior: 'show-only', patterns, mode: 'glob', target },
        },
        activeId: 'a-1',
        _migratedV1: true,
        _migratedV2: true,
      },
    }
  }

  test('the atom editor offers the Ticket and Entrypoint targets, default Title', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations: [],
      preferences: { filters: { nodes: {}, activeId: null, _migratedV1: true, _migratedV2: true } },
    })
    await withNetRetry(page, () => page.goto('/'))
    await page.getByTestId('active-filter-select').click()
    await page.getByTestId('active-filter-manage').click()
    await page.getByTestId('manage-filters-new').click()
    const target = page.getByTestId('filter-editor-target')
    await expect(target).toHaveValue('title')
    await expect(target.locator('option[value="ticket"]')).toHaveText('Ticket')
    await expect(target.locator('option[value="entrypoint"]')).toHaveText('Entrypoint')
  })

  test('a saved ticket atom filters the Conversation List', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: rows, preferences: seed('ticket', ['fixture-org/alpha#84']) })
    await withNetRetry(page, () => page.goto('/'))
    await expect(page.getByText('Alpha session', { exact: true })).toBeVisible()
    await expect(page.getByText('Beta session', { exact: true })).toHaveCount(0)
  })

  test('a saved entrypoint atom filters the Conversation List', async ({ page, mockBackend }) => {
    await mockBackend({ conversations: rows, preferences: seed('entrypoint', ['sdk-*']) })
    await withNetRetry(page, () => page.goto('/'))
    await expect(page.getByText('Beta session', { exact: true })).toBeVisible()
    await expect(page.getByText('Alpha session', { exact: true })).toHaveCount(0)
  })
})

test.describe('Ticket UI round 3 (T1)', () => {
  // T1 UI round 3 (_orchestration/T1-UI.md).
  test.use({ timezoneId: 'UTC', locale: 'en-US' })

  test('V1: session times use the header date format', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/84')
    const rows = page.getByTestId('ticket-session')
    await expect(rows).toHaveCount(2)
    await expect(rows.nth(0)).toContainText('Nov 20, 2026, 9:05:02 AM')
    await expect(rows.nth(1)).toContainText('Nov 20, 2026, 9:01:06 AM')
    await expect(rows.nth(0)).not.toContainText(/\d{1,2}\/\d{1,2}\/\d{4}/)
  })

  test('V2: a title that equals a message shows once, as the message', async ({ page, mockBackend }) => {
    const body = {
      ...ALPHA_84,
      sessions: [
        session(S1, {
          kinds: ['message', 'title'],
          refs: [
            { kind: 'title', message_uuid: null, snippet: 'Fix #84 in the search' },
            { kind: 'message', message_uuid: 's1-prompt', snippet: '  Fix #84 in the search\n' },
            { kind: 'message', message_uuid: 's1-reply', snippet: 'Done with #84.' },
          ],
        }),
      ],
    }
    await serve(page, mockBackend, { tickets: { 'fixture-org/alpha/84': { status: 200, body } } })
    await openTicket(page, 'fixture-org/alpha/84')
    const refs = page.getByTestId('ticket-ref')
    await expect(refs).toHaveCount(2)
    await expect(refs.nth(0)).toContainText('title and message:')
    await expect(refs.nth(0).getByRole('link')).toHaveAttribute('href', `/conversations/${S1}?m=s1-prompt`)
    await expect(refs.nth(1).getByRole('link')).toHaveAttribute('href', `/conversations/${S1}?m=s1-reply`)
    await expect(page.getByText('title:', { exact: true })).toHaveCount(0)
  })

  test('V2: a title that differs from every message still shows on its own', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/84')
    const refs = page.getByTestId('ticket-session').nth(1).getByTestId('ticket-ref')
    await expect(refs).toHaveCount(3)
    await expect(refs.filter({ hasText: TITLES[S1] }).getByRole('link')).toHaveCount(0)
  })

  test('V3: the header shows PRs apart from tickets', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await withNetRetry(page, () => page.goto(`/conversations/${S1}`))
    const header = page.locator('header').first()
    await expect(header.getByTestId('tickets-row').getByTestId('ticket-link')).toHaveText(['fixture-org/alpha#84'])
    const pr = header.getByTestId('prs-row').getByTestId('pr-link')
    await expect(pr).toHaveText(['fixture-org/alpha#85'])
    await expect(pr).toHaveAttribute('href', 'https://github.com/fixture-org/alpha/pull/85')
    await expect(pr).toHaveAttribute('target', '_blank')
  })

  test('V4: a GitHub ticket page links to the issue on GitHub', async ({ page, mockBackend }) => {
    await serve(page, mockBackend)
    await openTicket(page, 'fixture-org/alpha/84')
    const link = page.getByRole('link', { name: 'Open in GitHub' })
    await expect(link).toHaveAttribute('href', 'https://github.com/fixture-org/alpha/issues/84')
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(link).toHaveAttribute('rel', /noopener/)
    await expect(link).toHaveAttribute('rel', /noreferrer/)
  })

  test('V4: the GitHub link comes from the ticket key, never from a URL in the data', async ({ page, mockBackend }) => {
    const body = { ...ALPHA_84, url: 'https://evil.example/fixture-org/alpha/issues/84' }
    await serve(page, mockBackend, { tickets: { 'fixture-org/alpha/84': { status: 200, body } } })
    await openTicket(page, 'fixture-org/alpha/84')
    await expect(page.getByRole('link', { name: 'Open in GitHub' })).toHaveAttribute(
      'href',
      'https://github.com/fixture-org/alpha/issues/84',
    )
  })

  test('V4: the unknown-repo page has no GitHub link', async ({ page, mockBackend }) => {
    await serve(page, mockBackend, {
      tickets: { '_/_/84': { status: 200, body: { ...ALPHA_84, key: '#84', repo: null, url: null, prs: [] } } },
    })
    await openTicket(page, '_/_/84')
    await expect(page.getByTestId('ticket-heading')).toHaveText('#84 (unknown repo)')
    await expect(page.getByTestId('ticket-session')).toHaveCount(2)
    await expect(page.getByRole('link', { name: /Open in GitHub/ })).toHaveCount(0)
  })

  test('V4: an owner that fails the repo check gives no GitHub link', async ({ page, mockBackend }) => {
    await serve(page, mockBackend, {
      tickets: { '-bad/alpha/84': { status: 200, body: { ...ALPHA_84, key: '-bad/alpha#84', repo: '-bad/alpha' } } },
    })
    await openTicket(page, '-bad/alpha/84')
    await expect(page.getByTestId('ticket-session')).toHaveCount(2)
    await expect(page.getByRole('link', { name: /Open in GitHub/ })).toHaveCount(0)
  })
})
