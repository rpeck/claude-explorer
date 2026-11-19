/**
 * N8-ui test helpers (PLANS/2026.11.18-codex-source.md, N8; wave 4 brief).
 *
 * This branch has no Codex descriptor in `sources.generated.json`; N8-backend
 * adds it. The tests put a fake one into the registry with `setRegistry`.
 *
 * Import the code under test with `importModule` after `setRegistry(...)`,
 * so that the module graph sees the test registry.
 */
import { REAL, setRegistry, type Descriptor } from '../sources/fakeRegistry'

/** The Codex descriptor of plan §4.1 ("The Codex descriptor"). */
export const CODEX: Descriptor = {
  id: 'CODEX',
  label: 'Codex',
  short_label: 'Codex',
  chronological: true,
  has_projects: true,
  has_archive_state: true,
  has_organizations: false,
  refetchable: false,
  messages_change_in_place: true,
}

/** The real registry plus CODEX last (registry order). */
export function setCodexRegistry(): void {
  setRegistry([...REAL.filter((d) => d.id !== CODEX.id).map((d) => ({ ...d })), { ...CODEX }])
}

/** The real registry without CODEX: the preset fallback applies. */
export function setRegistryWithoutCodex(): void {
  setRegistry(REAL.filter((d) => d.id !== CODEX.id).map((d) => ({ ...d })))
}

export async function importModule<T = Record<string, unknown>>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T
}

/** Node 22+ hides jsdom's localStorage; on Node 20 it leaks between tests. */
export function clearLocalStorage(): void {
  try {
    window.localStorage?.clear()
  } catch {
    // no storage: nothing to clear
  }
}

/** A detail payload; the fields of plan §4.5 are in the overrides. */
export function detail(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    uuid: 'conv-uuid-1',
    name: 'A session',
    source: 'CLAUDE_AI',
    model: 'm',
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    message_count: 2,
    has_branches: false,
    messages: [],
    compact_markers: [],
    prelude_hidden_count: 0,
    file_path: null,
    project_path: null,
    project_name: null,
    git_branch: null,
    sandbox_path: null,
    error: null,
    ...overrides,
  }
}

export const THREAD_ID = '019a1b2c-3d4e-7f60-8a9b-0c1d2e3f4a5b'
export const HOME_ABS = '/home/fixture/.codex'
export const HOME_SHOWN = '~/.codex'

export function codexDetail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return detail({
    uuid: THREAD_ID,
    name: 'Fix the parser',
    source: 'CODEX',
    model: 'gpt-5.6-codex',
    project_path: '/home/fixture/work/parser',
    project_name: 'parser',
    file_path: `/data/codex/abc123def456/threads/${THREAD_ID}.json`,
    source_name: 'Fix the parser',
    source_home: HOME_SHOWN,
    source_home_abs: HOME_ABS,
    notices: [],
    parent_uuid: null,
    ...overrides,
  })
}
