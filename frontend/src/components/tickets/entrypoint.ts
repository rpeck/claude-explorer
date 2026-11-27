/**
 * Issue #93: the label of a launcher value (Claude Code `entrypoint`, Codex
 * `originator`). An unknown value shows as it is (plan §2.5.2).
 */
const LABELS: Record<string, string> = {
  cli: 'CLI',
  'claude-desktop': 'Desktop',
  'claude-vscode': 'VS Code',
  'sdk-cli': 'SDK',
  'sdk-ts': 'SDK',
  'sdk-py': 'SDK',
  'claude-code-github-action': 'GitHub Action',
  codex_cli_rs: 'Codex CLI',
  codex_exec: 'Codex exec',
  codex_vscode: 'Codex VS Code',
}

export function entrypointLabel(raw: string): string {
  return Object.hasOwn(LABELS, raw) ? LABELS[raw] : raw
}
