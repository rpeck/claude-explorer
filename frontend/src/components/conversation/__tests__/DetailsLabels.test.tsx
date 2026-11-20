/**
 * D1 (wave 9, issue #84): every row in the Details section of the
 * conversation header has a label, as the Codex rows do
 * (`<span className="text-zinc-500">Label:</span>`).
 *
 *   - "Project:" in front of the project path.
 *   - "Branch:" in front of the git branch, on the project row.
 *   - "Session ID:" in front of the UUID (Codex keeps "Thread ID:").
 *   - "File:" in front of the file path.
 *   - A label is in the row of its value, before the copy button, and not
 *     inside it: the copy button copies the value only.
 *   - A row without a value shows no label. The existing labels do not change.
 *
 * D1 is not in ConversationHeader.tsx yet: the tests of a new label are strict
 * expected failures (`it.fails`) until the tester's final commit. RUNXFAIL=1
 * runs them as plain tests.
 */
import { createElement, type ComponentType } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { serveApp } from '@/test/sources/fakeRegistry'
import {
  THREAD_ID,
  clearLocalStorage,
  codexDetail,
  detail,
  importModule,
  setCodexRegistry,
} from '@/test/codex/codexHelpers'

const xit = import.meta.env.RUNXFAIL === '1' ? it : it.fails

const UUID = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0'
const PROJECT = '/home/fixture/work/a-project'
const BRANCH = 'feat/details-labels'
const FILE = `/home/fixture/.claude/projects/-home-fixture-work-a-project/${UUID}.jsonl`
const DESKTOP_FILE = 'C:\\Users\\fixture\\.claude-explorer\\conversations\\conv.json'

const TITLES = {
  project: 'Click to copy project path',
  branch: 'Click to copy git branch',
  uuid: 'Click to copy UUID',
  file: 'Click to copy file path',
  thread: 'Click to copy thread ID',
  sandbox: 'Click to copy sandbox path',
  codexName: 'Click to copy Codex name',
  codexHome: 'Click to copy Codex home',
}

function claudeCode(overrides: Record<string, unknown> = {}) {
  return detail({
    source: 'CLAUDE_CODE',
    uuid: UUID,
    project_path: PROJECT,
    project_name: 'a-project',
    git_branch: BRANCH,
    file_path: FILE,
    ...overrides,
  })
}

async function renderHeader(conversation: Record<string, unknown>) {
  const { AppProviders } = await importModule<{ AppProviders: ComponentType<{ children: React.ReactNode }> }>(
    '@/test/sources/harness',
  )
  const { ConversationHeader } = await importModule<{ ConversationHeader: ComponentType<Record<string, unknown>> }>(
    '@/components/conversation/ConversationHeader',
  )
  serveApp({})
  render(
    createElement(AppProviders, null, createElement(ConversationHeader, { conversation, onOpenTree: vi.fn() })),
  )
  await screen.findByRole('heading', { level: 1 })
}

function stubClipboard() {
  const spy = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve())
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: spy } })
  return spy
}

/** The label is a muted span, alone, in the row of its copy button, before it. */
function expectLabel(label: string, buttonTitle: string): HTMLElement {
  const span = screen.getByText(label, { exact: true })
  expect(span.tagName).toBe('SPAN')
  expect(span).toHaveClass('text-zinc-500')
  const button = screen.getByTitle(buttonTitle)
  expect(button.contains(span), `"${label}" must not be inside the copy button`).toBe(false)
  expect(span.parentElement?.contains(button), `"${label}" must be in the row of its value`).toBe(true)
  expect(span.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  return span
}

/** The copy button shows and copies the value, without the label. */
async function expectCopiesValueOnly(buttonTitle: string, value: string) {
  const spy = stubClipboard()
  const button = screen.getByTitle(buttonTitle)
  expect(button.textContent?.trim()).toBe(value)
  await act(async () => {
    fireEvent.click(button)
  })
  expect(spy).toHaveBeenCalledTimes(1)
  expect(spy).toHaveBeenCalledWith(value)
}

beforeEach(() => {
  clearLocalStorage()
  setCodexRegistry()
})

describe('Details labels: Claude Code', () => {
  xit('"Project:" is in front of the project path', async () => {
    await renderHeader(claudeCode())
    expectLabel('Project:', TITLES.project)
  })

  xit('"Branch:" is in front of the git branch', async () => {
    await renderHeader(claudeCode())
    expectLabel('Branch:', TITLES.branch)
  })

  xit('the git branch stays on the project row', async () => {
    await renderHeader(claudeCode())
    const project = expectLabel('Project:', TITLES.project)
    const branch = expectLabel('Branch:', TITLES.branch)
    expect(branch.parentElement).toBe(project.parentElement)
    expect(project.compareDocumentPosition(branch) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByTitle(TITLES.project).compareDocumentPosition(branch) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  xit('"Session ID:" is in front of the UUID', async () => {
    await renderHeader(claudeCode())
    expectLabel('Session ID:', TITLES.uuid)
  })

  xit('"File:" is in front of the file path', async () => {
    await renderHeader(claudeCode())
    expectLabel('File:', TITLES.file)
  })

  it('the project path copy button copies the path only', async () => {
    await renderHeader(claudeCode())
    await expectCopiesValueOnly(TITLES.project, PROJECT)
  })

  it('the git branch copy button copies the branch only', async () => {
    await renderHeader(claudeCode())
    await expectCopiesValueOnly(TITLES.branch, BRANCH)
  })

  it('the UUID copy button copies the UUID only', async () => {
    await renderHeader(claudeCode())
    await expectCopiesValueOnly(TITLES.uuid, UUID)
  })

  it('the file path copy button copies the path only', async () => {
    await renderHeader(claudeCode())
    await expectCopiesValueOnly(TITLES.file, FILE)
  })

  xit('no git branch: no "Branch:" label, and "Project:" stays', async () => {
    await renderHeader(claudeCode({ git_branch: null }))
    expectLabel('Project:', TITLES.project)
    expect(screen.queryByText('Branch:')).toBeNull()
  })

  it('no project path: no "Project:" and no "Branch:" label', async () => {
    await renderHeader(claudeCode({ project_path: null, project_name: null }))
    expect(screen.queryByText('Project:')).toBeNull()
    expect(screen.queryByText('Branch:')).toBeNull()
  })

  xit('no file path: no "File:" label, and "Session ID:" stays', async () => {
    await renderHeader(claudeCode({ file_path: null }))
    expectLabel('Session ID:', TITLES.uuid)
    expect(screen.queryByText('File:')).toBeNull()
  })
})

describe('Details labels: Claude Desktop', () => {
  xit('"Session ID:" and "File:" label the UUID and the file path', async () => {
    await renderHeader(detail({ source: 'CLAUDE_AI', uuid: UUID, file_path: DESKTOP_FILE }))
    expectLabel('Session ID:', TITLES.uuid)
    expectLabel('File:', TITLES.file)
  })

  it('the file path copy button copies a Windows path only', async () => {
    await renderHeader(detail({ source: 'CLAUDE_AI', uuid: UUID, file_path: DESKTOP_FILE }))
    await expectCopiesValueOnly(TITLES.file, DESKTOP_FILE)
  })

  it('no project: no "Project:" and no "Branch:" label', async () => {
    await renderHeader(detail({ source: 'CLAUDE_AI', uuid: UUID, file_path: DESKTOP_FILE }))
    expect(screen.queryByText('Project:')).toBeNull()
    expect(screen.queryByText('Branch:')).toBeNull()
  })

  xit('no file path: "Session ID:" only', async () => {
    await renderHeader(detail({ source: 'CLAUDE_AI', uuid: UUID, file_path: null }))
    expectLabel('Session ID:', TITLES.uuid)
    expect(screen.queryByText('File:')).toBeNull()
  })
})

describe('Details labels: Cowork', () => {
  const cowork = (overrides: Record<string, unknown> = {}) =>
    detail({
      source: 'CLAUDE_COWORK',
      uuid: 'local_6f2c1d0e-1111-2222-3333-444455556666',
      sandbox_path: '/sessions/vm-7/work',
      file_path: '/home/fixture/cowork/local_6f2c1d0e/audit.jsonl',
      ...overrides,
    })

  xit('"Session ID:" is in front of the session UUID', async () => {
    await renderHeader(cowork())
    expectLabel('Session ID:', TITLES.uuid)
  })

  xit('"File:" is in front of the file path', async () => {
    await renderHeader(cowork())
    expectLabel('File:', TITLES.file)
  })

  it('"Sandbox path:" does not change', async () => {
    await renderHeader(cowork())
    expectLabel('Sandbox path:', TITLES.sandbox)
    await expectCopiesValueOnly(TITLES.sandbox, '/sessions/vm-7/work')
  })
})

describe('Details labels: Codex', () => {
  xit('"Project:" is in front of the project path', async () => {
    await renderHeader(codexDetail())
    expectLabel('Project:', TITLES.project)
  })

  xit('"File:" is in front of the file path', async () => {
    await renderHeader(codexDetail())
    expectLabel('File:', TITLES.file)
  })

  it('keeps "Thread ID:", and has no "Session ID:" label', async () => {
    await renderHeader(codexDetail())
    expectLabel('Thread ID:', TITLES.thread)
    expect(screen.queryByText('Session ID:')).toBeNull()
    await expectCopiesValueOnly(TITLES.thread, THREAD_ID)
  })

  it('"Codex name:" and "Codex home:" do not change', async () => {
    await renderHeader(codexDetail())
    expectLabel('Codex name:', TITLES.codexName)
    expectLabel('Codex home:', TITLES.codexHome)
  })
})
