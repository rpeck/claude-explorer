/**
 * Issue #93: fill the optional "start a session" command template. Explorer
 * copies the result and never runs it (plan §2.5.2).
 *
 * - {repo} and {issue} are checked (owner/repo, digits); an invalid value
 *   becomes "".
 * - {title} and {cwd} each fill as one POSIX single-quoted word. Inside
 *   double quotes, interactive bash and zsh still run history expansion
 *   ("!#"), so a title could close the quote and run a command; single
 *   quotes stop every expansion. A newline becomes a space first.
 * - A title that starts with "-" gets a leading space, so no program reads
 *   it as a flag. A cwd that is not an absolute path fills as ''.
 * - So the template must put {title} and {cwd} outside quotes
 *   (placeholdersInQuotes).
 */
export interface StartCommandValues {
  repo: string | null
  issue: number | string
  title: string
  cwd: string
}

export const REPO_RE = /^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+$/
const ABSOLUTE_RE = /^(?:\/|[A-Za-z]:[\\/])/

/** One POSIX single-quoted word: ' + the value with each ' as '\'' + '. */
export function shellQuote(value: string): string {
  return "'" + value.replace(/\r\n|\n|\r/g, ' ').replace(/'/g, "'\\''") + "'"
}

/**
 * True when {title} or {cwd} stands inside a '...' or "..." of the template.
 * The scan follows POSIX rules: a backslash escapes the next character
 * outside single quotes, and nothing is special inside single quotes.
 */
export function placeholdersInQuotes(template: string): boolean {
  let quote: "'" | '"' | null = null
  for (let i = 0; i < template.length; i++) {
    const c = template[i]
    if (quote !== "'" && c === '\\') {
      i++
      continue
    }
    if (quote === null && (c === "'" || c === '"')) quote = c
    else if (quote !== null && c === quote) quote = null
    else if (quote !== null && (template.startsWith('{title}', i) || template.startsWith('{cwd}', i))) return true
  }
  return false
}

export function fillStartCommand(template: string, values: StartCommandValues): string {
  const issue = String(values.issue)
  const title = values.title ?? ''
  const cwd = values.cwd ?? ''
  const filled: Record<string, string> = {
    repo: values.repo && REPO_RE.test(values.repo) ? values.repo : '',
    issue: /^\d+$/.test(issue) ? issue : '',
    title: shellQuote(title.startsWith('-') ? ' ' + title : title),
    cwd: ABSOLUTE_RE.test(cwd) ? shellQuote(cwd) : "''",
  }
  // One pass, so a value that holds "{issue}" is not filled again.
  return template.replace(/\{(repo|issue|title|cwd)\}/g, (_m, name: string) => filled[name])
}
