/**
 * useBookmarkHotkey — 'b' / 'B' keyboard listener that toggles a
 * bookmark on the currently focused conversation message.
 *
 * Standalone hook (separate from useMessageNavigationRegistry, Commit
 * 7a) because the listener's dependency set is unrelated — it depends
 * on `conversation` + `toggleBookmark` + the focus accessor, not on
 * the visibleMessages / virtualizer / showPrelude inputs the registry
 * needs.
 *
 * Guards (preserved verbatim from the pre-extraction site):
 *   - No listener mounted when there's no conversation.
 *   - Typing inside <input>, <textarea>, or contenteditable: ignored
 *     so the letter 'b' reaches the field.
 *   - Modifier keys (Cmd/Ctrl/Alt) skip the handler — those collide
 *     with browser nav shortcuts.
 *   - Only 'b' and 'B' (capslock / shift) trigger.
 *   - No focused message → no-op.
 *
 * Extracted from ConversationPage.tsx (2026-05-31, Commit 7b of
 * PLANS/2026.05.31-conversationpage-decomposition.md). Behavior-preserving.
 */
import { useEffect } from 'react'
import type { ConversationDetail } from '@/lib/types'
import type { Bookmark } from '@/lib/types'

interface UseBookmarkHotkeyArgs {
  conversation: ConversationDetail | null | undefined
  getSelectedMessageId: () => string | null
  toggleBookmark: (input: Omit<Bookmark, 'id' | 'created_at'>) => Promise<void>
}

export function useBookmarkHotkey({
  conversation,
  getSelectedMessageId,
  toggleBookmark,
}: UseBookmarkHotkeyArgs): void {
  useEffect(() => {
    if (!conversation) return
    const handler = (e: KeyboardEvent) => {
      // Hunt #2: e.target is EventTarget; reading .tagName /
      // .isContentEditable needs an HTMLElement narrowing.
      if (
        e.target instanceof HTMLElement &&
        (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable)
      ) {
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key !== 'b' && e.key !== 'B') return
      const selectedId = getSelectedMessageId()
      if (!selectedId) return
      const msg = conversation.messages.find((m) => m.uuid === selectedId)
      if (!msg) return
      e.preventDefault()
      toggleBookmark({
        conversation_id: conversation.uuid,
        message_uuid: msg.uuid,
        source: conversation.source === 'CLAUDE_AI' ? 'claude_desktop' : 'claude_code',
        note: '',
        snippet: (msg.text || '').slice(0, 140),
        // Issue #96: on an agent page, the bookmark names the agent. A
        // session's bookmark keeps the body it had before.
        ...(conversation.agent_id
          ? { agent_id: conversation.agent_id, agent_description: conversation.agent_description ?? null }
          : {}),
      })
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [conversation, getSelectedMessageId, toggleBookmark])
}
