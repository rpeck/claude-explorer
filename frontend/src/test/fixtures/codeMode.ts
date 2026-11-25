/**
 * Synthetic Code Mode data (issue #90) for vitest and Playwright. The shape
 * is what the backend sends for a Claude Code `REPL` run
 * (backend/cc_code_mode.py); every value is made up.
 */
import type { ContentBlock, ConversationDetail, ConversationSummary, Message } from '@/lib/types'

export const CM_CONV_UUID = 'c0de0000-0000-4000-8000-0000000000e2'
export const CM_RUN_ID = 'toolu_01CMVIEW0000000000000001'
export const CM_SECOND_RUN_ID = 'toolu_01CMVIEW0000000000000002'
export const CM_WORDS = {
  code: 'cmviewscript',
  output: 'cmviewoutput',
  returned: 'cmviewreturned',
  reply: 'cmviewreply',
} as const

/** 21 lines: the bubble cuts it after 15. */
export const CM_CODE = [
  `// ${CM_WORDS.code}`,
  "const ls = await $`ls src`.text();",
  "const app = await tools.Read({ file_path: 'src/app.py' });",
  ...Array.from({ length: 17 }, (_, i) => `const step${i} = ${i};`),
  "({ files: ls.trim().split('\\n') })",
].join('\n')

export function codeRunBlock(overrides: Partial<NonNullable<ContentBlock['code_run']>> = {}, id = CM_RUN_ID): ContentBlock {
  return {
    type: 'code_run',
    id,
    name: 'REPL',
    code_run: {
      language: 'javascript',
      code: CM_CODE,
      line_count: 21,
      description: 'List src and read app.py',
      status: 'completed',
      background: false,
      result: `{ files: ['app.py', '${CM_WORDS.returned}.py'] }`,
      stdout: 'listing done',
      stderr: null,
      error: null,
      refused: null,
      inner_calls: [
        {
          id: 'toolu_cc_view00000000000000000000001', name: 'Bash', input: { command: 'ls src' },
          result: `app.py\n${CM_WORDS.output}.py`, is_error: false, refused: false,
          code_run_parent: id, link_source: 'transcript_field',
        },
        {
          id: 'toolu_cc_view00000000000000000000002', name: 'Write', input: { file_path: 'src/new.txt', content: 'hi' },
          result: 'Permission denied for Write', is_error: true, refused: true,
          code_run_parent: id, link_source: 'transcript_field',
        },
      ],
      ...overrides,
    },
  }
}

function message(uuid: string, sender: 'human' | 'assistant', content: ContentBlock[], minute: number): Message {
  const when = `2026-11-24T10:${String(minute).padStart(2, '0')}:00Z`
  return {
    uuid, sender, content, created_at: when, updated_at: when, truncated: false,
    text: content.filter((b) => b.type === 'text').map((b) => b.text).join('\n'),
    parent_message_uuid: null, attachments: [], files: [], files_v2: [],
  } as Message
}

export const CM_MESSAGES: Message[] = [
  message('cm-u1', 'human', [{ type: 'text', text: 'List the files.' }], 0),
  message('cm-a1', 'assistant', [codeRunBlock()], 1),
  message('cm-a2', 'assistant', [{ type: 'text', text: `There are two files (${CM_WORDS.reply}).` }], 2),
  message('cm-a3', 'assistant', [
    { type: 'tool_use', id: 'exec-00000000-0000-4000-8000-0000000000d3', name: 'shell', input: { command: 'ls' },
      from_code_mode: true, ambiguous_with: [CM_RUN_ID, CM_SECOND_RUN_ID] },
    { type: 'tool_result', tool_use_id: 'exec-00000000-0000-4000-8000-0000000000d3', content: [{ type: 'text', text: 'src' }] },
  ], 3),
]

export const CM_SUMMARY: ConversationSummary = {
  uuid: CM_CONV_UUID,
  name: 'Code Mode fixture session',
  model: 'claude-sonnet-5-5',
  created_at: '2026-11-24T10:00:00Z',
  updated_at: '2026-11-24T10:03:00Z',
  is_starred: false,
  message_count: CM_MESSAGES.length,
  source: 'CLAUDE_CODE',
  project_path: '/Users/tester/Source/cm',
  project_name: 'cm',
} as ConversationSummary

export const CM_DETAIL: ConversationDetail = {
  ...CM_SUMMARY,
  summary: '',
  current_leaf_message_uuid: 'cm-a3',
  has_branches: false,
  messages: CM_MESSAGES,
  compact_markers: [],
} as ConversationDetail

/** A pipe: the fetch output (147 characters) goes into the Bash call; only "31" returns. */
export const CM_PAGE = 'Contents of https://example.com/:\n# Example Domain\n\nThis domain is for use in documentation examples without needing permission. Do not rely on it.'

export function pipeRunBlock(overrides: Partial<NonNullable<ContentBlock['code_run']>> = {}): ContentBlock {
  const id = 'toolu_01CMPIPE0000000000000001'
  return codeRunBlock({
    code: 'const page = await tools.mcp__fetch__fetch({url: "https://example.com"});\nawait $`wc -w <<EOF\n${page}\nEOF`.text()',
    line_count: 2,
    result: '31',
    stdout: null,
    inner_calls: [
      { id: 'toolu_cc_pipe1', name: 'mcp__fetch__fetch', input: { url: 'https://example.com' }, result: CM_PAGE,
        is_error: false, refused: false, code_run_parent: id, link_source: 'transcript_field', inputs_from: [] },
      { id: 'toolu_cc_pipe2', name: 'Bash', input: { command: `wc -w <<EOF\n${CM_PAGE}\nEOF` }, result: '      31',
        is_error: false, refused: false, code_run_parent: id, link_source: 'transcript_field', inputs_from: [0] },
    ],
    returned_from: [],
    ...overrides,
  }, id)
}
