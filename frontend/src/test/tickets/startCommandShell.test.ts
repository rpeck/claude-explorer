/// <reference types="node" />
/**
 * T1 security round 2 (issue #93): the start command in real shells.
 * The contract (_orchestration/T1-SECURITY.md, T1):
 *
 *   - {title} and {cwd} each fill as one POSIX single-quoted word; each ' is
 *     written as '\''. A newline (\r\n, \n, \r) becomes a space first.
 *   - A {title} that starts with "-" gets a leading space inside the quotes.
 *   - A {cwd} that is not an absolute path fills as ''.
 *   - So a filled command, run in interactive bash (history expansion on) and
 *     zsh (BANG_HIST on), prints each hostile value as one argument.
 *
 * The shells run with an empty HOME and no rc files, in a new temp folder; a
 * shell that is not installed is skipped. The hostile values only touch files
 * in that folder.
 *
 */
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { importModule } from '@/test/codex/codexHelpers'


type Values = { repo: string | null; issue: number; title: string; cwd: string }
type Fill = (template: string, values: Values) => string

async function fill(): Promise<Fill> {
  return (await importModule<{ fillStartCommand: Fill }>('@/components/tickets/startCommand')).fillStartCommand
}

const BASE: Values = { repo: 'fixture-org/alpha', issue: 84, title: 'Fix search', cwd: '/home/fixture/alpha' }

const HOSTILE = [
  '!#; touch pwned #',
  '!!',
  '!-1',
  '$(touch pwned)',
  '`touch pwned`',
  "'; touch pwned; '",
  "it's",
  '"',
  '\\',
  'line one\nline two',
  'a\r\nb',
  'c\rd',
  '-x',
  '--settings={"permissions":{"allow":["Bash"]}}',
  '{issue}',
]

/** What the shell must print for a {title} value. */
function expected(value: string): string {
  const flat = value.replace(/\r\n|\n|\r/g, ' ')
  return flat.startsWith('-') ? ' ' + flat : flat
}

const SHELLS = {
  bash: { args: ['--norc', '--noprofile', '-i', '-o', 'histexpand'] },
  zsh: { args: ['-f', '-i', '-o', 'BANG_HIST'] },
} as const

function installed(shell: string): boolean {
  return spawnSync('/bin/sh', ['-c', `command -v ${shell}`]).status === 0
}

/** Run one command line in an interactive shell; return the lines between the markers, and the new files. */
function run(shell: keyof typeof SHELLS, line: string): { out: string[]; files: string[] } {
  const dir = mkdtempSync(join(tmpdir(), 't1-shell-'))
  const input = `printf '%s\\n' __BEGIN__; ${line}; printf '%s\\n' __END__\nexit\n`
  const r = spawnSync(shell, [...SHELLS[shell].args], {
    cwd: dir,
    input,
    encoding: 'utf8',
    env: { HOME: dir, PATH: '/usr/bin:/bin', HISTFILE: '', PS1: '', LANG: 'C.UTF-8' },
    timeout: 10_000,
  })
  const lines = (r.stdout ?? '').split('\n')
  const begin = lines.findIndex((l: string) => l.endsWith('__BEGIN__'))
  const end = lines.findIndex((l: string, i: number) => i > begin && l.endsWith('__END__'))
  const out = begin >= 0 && end > begin ? lines.slice(begin + 1, end) : ['<no markers>', r.stdout ?? '', r.stderr ?? '']
  return { out, files: existsSync(dir) ? readdirSync(dir) : [] }
}

for (const shell of Object.keys(SHELLS) as (keyof typeof SHELLS)[]) {
  describe.skipIf(!installed(shell))(`the filled command in ${shell}`, () => {
    for (const value of HOSTILE) {
      it(`prints the title ${JSON.stringify(value)} as one argument`, async () => {
        const f = await fill()
        const { out, files } = run(shell, f("printf '%s\\n' {title}", { ...BASE, title: value }))
        expect(out).toEqual([expected(value)])
        expect(files).toEqual([])
      })
    }

    it('prints a hostile cwd as one argument', async () => {
      const f = await fill()
      const cwd = "/home/fixture/$(touch pwned) `touch pwned` !# 'q' \"d\""
      const { out, files } = run(shell, f("printf '%s\\n' {cwd}", { ...BASE, cwd }))
      expect(out).toEqual([cwd])
      expect(files).toEqual([])
    })

    it('keeps the title and cwd apart from the rest of the command', async () => {
      const f = await fill()
      const line = f('printf \'%s\\n\' "Fix {repo}#{issue}: "{title} {cwd}', {
        ...BASE,
        title: '!#; touch pwned #',
        cwd: '/home/fixture/a b',
      })
      const { out, files } = run(shell, line)
      expect(out).toEqual(['Fix fixture-org/alpha#84: !#; touch pwned #', '/home/fixture/a b'])
      expect(files).toEqual([])
    })
  })
}

describe('fillStartCommand single-quotes the free text', () => {
  it('a title is one single-quoted word', async () => {
    const f = await fill()
    expect(f('{title}', { ...BASE, title: 'Fix search' })).toBe("'Fix search'")
  })

  it("a ' in a value is written as '\\''", async () => {
    const f = await fill()
    expect(f('{title}', { ...BASE, title: "it's" })).toBe("'it'\\''s'")
  })

  it('every newline form becomes a space', async () => {
    const f = await fill()
    expect(f('{title}', { ...BASE, title: 'a\r\nb\nc\rd' })).toBe("'a b c d'")
  })

  it('a title that starts with "-" gets a leading space', async () => {
    const f = await fill()
    expect(f('{title}', { ...BASE, title: '-x' })).toBe("' -x'")
  })

  it('an empty title is an empty word', async () => {
    const f = await fill()
    expect(f('a {title} b', { ...BASE, title: '' })).toBe("a '' b")
  })

  it('a cwd is one single-quoted word', async () => {
    const f = await fill()
    expect(f('{cwd}', { ...BASE, cwd: '/home/fixture/a b' })).toBe("'/home/fixture/a b'")
  })

  it('a cwd that is not absolute fills as an empty word', async () => {
    const f = await fill()
    expect(f('{cwd}', { ...BASE, cwd: 'relative/path' })).toBe("''")
    expect(f('{cwd}', { ...BASE, cwd: '-rf' })).toBe("''")
  })

  it('a Windows drive path counts as absolute', async () => {
    const f = await fill()
    expect(f('{cwd}', { ...BASE, cwd: 'C:\\Users\\fixture' })).toBe("'C:\\Users\\fixture'")
  })
})
