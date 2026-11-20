// The display name of a model and the sender label of a reply (issue #52).
// The Python twin is `backend/model_names.py`; keep the two in step.
import type { Message, NoticeKind } from './types'

/** The model that Claude Code writes on the rows that it makes itself. */
export const SYNTHETIC_MODEL = '<synthetic>'

// claude-opus-4-5, claude-sonnet-5
const FAMILY_FIRST = /^claude-([a-z]+)-(\d+(?:-\d+)*)$/
// claude-3-5-sonnet (the older order)
const VERSION_FIRST = /^claude-(\d+(?:-\d+)*)-([a-z]+)$/

/** `claude-opus-4-5-20251101` → "Opus 4.5". Another id comes back as it is. */
export function modelDisplayName(id: string): string {
  if (id === SYNTHETIC_MODEL) return 'Claude Code'
  const bare = id.replace(/-\d{8}$/, '')
  const m = bare.match(FAMILY_FIRST)
  const old = bare.match(VERSION_FIRST)
  let family: string
  let version: string
  if (m) [, family, version] = m
  else if (old) [, version, family] = old
  else return id
  return `${family.charAt(0).toUpperCase()}${family.slice(1)} ${version.replaceAll('-', '.')}`
}

/** "Claude Opus 5.5"; "Claude Code" for a synthetic row; the assistant label alone with no model. */
export function replyLabel(assistantLabel: string, model: string | null | undefined): string {
  if (model === SYNTHETIC_MODEL) return 'Claude Code'
  if (!model) return assistantLabel
  return `${assistantLabel} ${modelDisplayName(model)}`
}

/** The tooltip of a reply label whose model comes from the conversation (D5). */
export function conversationModelTooltip(sourceLabel: string): string {
  return `From the conversation's model setting. ${sourceLabel} does not record the model of each reply.`
}

export const NOTICE_LABELS: Record<NoticeKind, string> = {
  'task-notification': 'Task notification',
  'bash-notification': 'Bash notification',
  'scheduled-task': 'Scheduled task',
  'system-reminder': 'System reminder',
}

/** The sender name of a message: the reply label, "You", or the kind of a
 *  row that Claude Code wrote. Twin of `sender_label` in model_names.py. */
export function senderLabel(message: Message, assistantLabel: string): string {
  if (message.notice_kind) return NOTICE_LABELS[message.notice_kind] ?? message.notice_kind
  if (message.shell_command || message.shell_output) return 'Shell'
  if (message.sender === 'human') return 'You'
  return replyLabel(assistantLabel, message.model)
}
