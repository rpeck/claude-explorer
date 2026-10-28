import { test, expect, makeSummary, makeMessage, makeDetail, withNetRetry } from './fixtures'

/**
 * Header layout regression: at narrow-but-realistic widths (≤ 1366px,
 * the common 13" laptop), the cluster of action buttons on the right
 * (Tools, Expand, Re-download, Hide/Show compact markers, Copy as
 * Markdown, Markdown, PDF) must not overlap the conversation
 * metadata block on the left (UUID copy button, file_path copy
 * button), nor the copy buttons for the session name, the project path,
 * the git branch, and the Cowork sandbox path
 * (PLANS/2026.10.28-header-copy-buttons.md, e2e 3).
 *
 * Manual repro 2026-05-01: at ~1366px the buttons wrap underneath the
 * metadata, occluding the UUID and file_path copy buttons (see
 * /Users/rpeck/.claude/image-cache/.../18.png).
 *
 * Pass condition: the bounding box of the rightmost button cluster
 * must not intersect the bounding box of any header copy button at any
 * of the tested widths.
 */

const HL = '00000000-0000-0000-0000-0000000000c3'

const summary = makeSummary({
  uuid: HL,
  name: 'Claude Desktop header layout fixture conversation with a long enough title to crowd the header',
  model: 'claude-opus-4-5-20251101',
  message_count: 1,
  human_message_count: 1,
  source: 'CLAUDE_CODE',
  project_path: '/Users/rpeck/Source/claude-desktop-message-exporter',
  project_name: 'claude-desktop-message-exporter',
  git_branch: 'feat/header-layout-with-a-long-branch-name',
})

const messages = [
  makeMessage({
    uuid: 'hl-m1',
    sender: 'human',
    text: 'Anything',
    content: [{ type: 'text', text: 'Anything' }],
  }),
]

const detail = makeDetail(summary, messages, {
  file_path: '/Users/rpeck/.claude/projects/-Users-rpeck-Source-claude-desktop-message-exporter/00000000-0000-0000-0000-0000000000c3.jsonl',
  compact_markers: [
    {
      message_uuid: 'hl-m1',
      summary_text: 'Tiny compact summary',
      timestamp: '2026-04-01T10:00:00Z',
      kind: 'auto',
      user_prompt: null,
    },
  ],
})

test.describe('Header layout — actions must not occlude metadata (Issue #3)', () => {
  // 1280, 1366, 1440 are the most common laptop widths. The bug shows
  // up clearly at 1280–1366; 1920 had no overlap, so we keep that as
  // a control case.
  for (const width of [1280, 1366, 1440] as const) {
    test(`buttons do not overlap UUID/file-path metadata at ${width}px`, async ({ page, mockBackend }) => {
      await page.setViewportSize({ width, height: 900 })
      await mockBackend({ conversations: [summary], details: { [HL]: detail } })
      await withNetRetry(page, () => page.goto(`/conversations/${HL}`))

      // Wait for the header to render. Scope to <header> from the
      // start: the sidebar ships its own <h1>Claude Explorer</h1>, so
      // an unscoped getByRole('heading', { level: 1 }) is a strict-mode
      // violation that blows up before any geometry assertion.
      const header = page.locator('header').first()
      await expect(header.getByRole('heading', { level: 1 })).toBeVisible()

      const uuidButton = page.getByTitle('Click to copy UUID')
      const filePathButton = page.getByTitle('Click to copy file path')
      const nameButton = header.getByRole('button', { name: 'Copy session name' })
      const projectPathButton = page.getByTitle('Click to copy project path')
      const gitBranchButton = page.getByTitle('Click to copy git branch')
      await expect(uuidButton).toBeVisible()
      await expect(filePathButton).toBeVisible()
      await expect(nameButton).toBeVisible()
      await expect(projectPathButton).toBeVisible()
      await expect(gitBranchButton).toBeVisible()

      // Metadata row that sits directly under the title: "Code" badge,
      // model badge, full date, message count, "View branches".
      // Scope to <header> so the sidebar's mini-card doesn't shadow.
      const codeBadge = header.getByText('Code', { exact: true })
      const modelBadge = header.getByText('claude-opus-4-5-20251101')
      const messageCount = header.getByText(/1 messages?/)
      await expect(codeBadge).toBeVisible()
      await expect(modelBadge).toBeVisible()

      // Pick a stable representative control from the right cluster.
      // 2026-05-25: Tools control converted from Button to
      // <label><input type="checkbox"></label>. The label wraps the icon
      // + visible "Show Tools" text and is the layout-meaningful unit,
      // so we boundingBox the label, not the 16×16 input.
      const toolsControl = page.getByTestId('header-show-tools-control')
      const markdownExportButton = page.getByRole('button', { name: 'Markdown', exact: true })
      const pdfExportButton = page.getByRole('button', { name: 'PDF', exact: true })
      await expect(toolsControl).toBeVisible()
      await expect(markdownExportButton).toBeVisible()
      await expect(pdfExportButton).toBeVisible()

      // Helper: rectangles A and B must not overlap.
      function intersects(
        a: { x: number; y: number; width: number; height: number },
        b: { x: number; y: number; width: number; height: number },
      ): boolean {
        return !(
          a.x + a.width <= b.x ||
          b.x + b.width <= a.x ||
          a.y + a.height <= b.y ||
          b.y + b.height <= a.y
        )
      }

      const metaBoxes = await Promise.all([
        codeBadge.boundingBox(),
        modelBadge.boundingBox(),
        messageCount.boundingBox(),
        uuidButton.boundingBox(),
        filePathButton.boundingBox(),
        nameButton.boundingBox(),
        projectPathButton.boundingBox(),
        gitBranchButton.boundingBox(),
      ])
      const buttonBoxes = await Promise.all([
        toolsControl.boundingBox(),
        markdownExportButton.boundingBox(),
        pdfExportButton.boundingBox(),
      ])

      for (const m of metaBoxes) {
        if (!m) continue
        for (const btn of buttonBoxes) {
          if (!btn) continue
          expect(
            intersects(m, btn),
            `metadata box ${JSON.stringify(m)} overlaps button box ${JSON.stringify(btn)} at ${width}px`,
          ).toBe(false)
        }
      }
    })
  }
})

// The sandbox path shows only for Cowork, so it needs its own fixture.
const HL_COWORK = '00000000-0000-0000-0000-0000000000c4'

const coworkSummary = makeSummary({
  uuid: HL_COWORK,
  name: 'Claude Cowork header layout fixture session with a long enough title to crowd the header',
  model: 'claude-opus-4-5-20251101',
  message_count: 1,
  human_message_count: 1,
  source: 'CLAUDE_COWORK',
})

const coworkDetail = makeDetail(
  coworkSummary,
  [
    makeMessage({
      uuid: 'hl-cw-m1',
      sender: 'human',
      text: 'Anything',
      content: [{ type: 'text', text: 'Anything' }],
    }),
  ],
  { sandbox_path: '/sessions/vm-header-layout-with-a-long-name/work/folder' },
)

test.describe('Header layout — actions must not cover the Cowork copy buttons', () => {
  for (const width of [1280, 1366, 1440] as const) {
    test(`buttons do not overlap the session name, UUID, or sandbox path buttons at ${width}px`, async ({
      page,
      mockBackend,
    }) => {
      await page.setViewportSize({ width, height: 900 })
      await mockBackend({ conversations: [coworkSummary], details: { [HL_COWORK]: coworkDetail } })
      await withNetRetry(page, () => page.goto(`/conversations/${HL_COWORK}`))

      const header = page.locator('header').first()
      await expect(header.getByRole('heading', { level: 1 })).toBeVisible()

      const copyButtons = [
        header.getByRole('button', { name: 'Copy session name' }),
        page.getByTitle('Click to copy UUID'),
        page.getByTitle('Click to copy sandbox path'),
      ]
      for (const b of copyButtons) await expect(b).toBeVisible()

      const actions = [
        page.getByTestId('header-show-tools-control'),
        page.getByRole('button', { name: 'Markdown', exact: true }),
        page.getByRole('button', { name: 'PDF', exact: true }),
      ]
      for (const a of actions) await expect(a).toBeVisible()

      const intersects = (
        a: { x: number; y: number; width: number; height: number },
        b: { x: number; y: number; width: number; height: number },
      ) => !(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y)

      const metaBoxes = await Promise.all(copyButtons.map((b) => b.boundingBox()))
      const buttonBoxes = await Promise.all(actions.map((a) => a.boundingBox()))
      for (const m of metaBoxes) {
        expect(m, 'copy button has a box').not.toBeNull()
        for (const btn of buttonBoxes) {
          expect(btn, 'action button has a box').not.toBeNull()
          expect(
            intersects(m!, btn!),
            `copy button box ${JSON.stringify(m)} overlaps action box ${JSON.stringify(btn)} at ${width}px`,
          ).toBe(false)
        }
      }
    })
  }
})
