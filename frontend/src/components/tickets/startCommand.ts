/**
 * Issue #93: fill the optional "start a session" command template. Explorer
 * copies the result and never runs it (plan §2.5.2).
 *
 * {repo} and {issue} are checked (owner/repo, digits); an invalid value
 * becomes "". {title} and {cwd} are free text: a backslash goes before
 * \ " $ and the backtick, so they stay literal inside double quotes in a
 * POSIX shell, and a newline becomes a space.
 */
export interface StartCommandValues {
  repo: string | null
  issue: number | string
  title: string
  cwd: string
}

const REPO_RE = /^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+$/

function escapeText(s: string): string {
  return s.replace(/[\\"$`]/g, (c) => '\\' + c).replace(/\r?\n|\r/g, ' ')
}

export function fillStartCommand(template: string, values: StartCommandValues): string {
  const issue = String(values.issue)
  const filled: Record<string, string> = {
    repo: values.repo && REPO_RE.test(values.repo) ? values.repo : '',
    issue: /^\d+$/.test(issue) ? issue : '',
    title: escapeText(values.title ?? ''),
    cwd: escapeText(values.cwd ?? ''),
  }
  // One pass, so a value that holds "{issue}" is not filled again.
  return template.replace(/\{(repo|issue|title|cwd)\}/g, (_m, name: string) => filled[name])
}
