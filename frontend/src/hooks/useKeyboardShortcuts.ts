import { useEffect, useCallback, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useSettings } from '@/contexts/SettingsContext'
import { useKeyboardNavigation } from '@/contexts/KeyboardNavigationContext'
import { useSearchPanel } from '@/contexts/SearchPanelContext'
import { useFetchPipeline } from '@/contexts/FetchPipelineContext'
import { queryKeys } from '@/lib/queryClient'
import { messageToMarkdown } from '@/lib/utils'
import { errorToast } from '@/lib/errorToast'
import type { ConversationDetail } from '@/lib/types'

function isInputElement(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  const tagName = target.tagName.toLowerCase()
  return (
    tagName === 'input' ||
    tagName === 'textarea' ||
    tagName === 'select' ||
    target.isContentEditable
  )
}

// Inputs that opt-in to letting specific global shortcuts (Cmd+K, Cmd+F,
// Cmd+G, Cmd+Shift+G, Escape) still fire even while they hold focus.
// The SearchPanel input sets this attribute so typing in it doesn't block
// its own navigation shortcuts.
function allowsShortcuts(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  return target.closest('[data-allow-shortcuts]') !== null
}

// Hunt #2: replaces two `document.querySelector(...) as HTMLInputElement`
// casts at the Vim '/' and Emacs Ctrl+S sites. querySelector returns
// `Element | null`; the placeholder selector could match a future
// non-input element, and the cast would silently call .focus() on
// something that may lack it. Guard with `instanceof HTMLInputElement`
// and no-op if the match isn't an actual input. Exported for testing.
export function focusSearchInput(): void {
  const el = document.querySelector('input[placeholder*="Search"]')
  if (el instanceof HTMLInputElement) el.focus()
}

export function useKeyboardShortcuts() {
  const navigate = useNavigate()
  const location = useLocation()
  const currentUuid = useMemo(() => {
    const match = location.pathname.match(/\/conversations\/(.+)/)
    return match?.[1]
  }, [location.pathname])
  const { keyboardMode, showToolCalls, rightPaneTab, setRightPaneTab } = useSettings()
  const queryClient = useQueryClient()
  const {
    selectNext,
    selectPrevious,
    selectFirst,
    selectLast,
    getSelectedId,
    setIsHelpOpen,
    focusArea,
    setFocusArea,
    navSource,
    setNavSource,
    selectNextMessage,
    selectPreviousMessage,
    selectFirstMessage,
    selectLastMessage,
    selectNextUserMessage,
    selectPreviousUserMessage,
    selectNextAssistantMessage,
    selectPreviousAssistantMessage,
    pageDown,
    pageUp,
    setSelectedMessageIndex,
    getSelectedMessageId,
  } = useKeyboardNavigation()
  const searchPanel = useSearchPanel()
  const { startRefresh, isRunning: isRefreshRunning } = useFetchPipeline()

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      // Lightbox / Radix Dialog wins when open. Without this guard, the
      // global Esc and ArrowLeft handlers below eat the keys before the
      // dialog's own listener ever runs (manual finding 2026-05-04 —
      // user reported Esc didn't close the lightbox and ←→ didn't
      // navigate between images). Detect via Radix's stable
      // [data-state="open"][role="dialog"] selector.
      if (
        document.querySelector('[role="dialog"][data-state="open"]') &&
        (e.key === 'Escape' ||
          e.key === 'ArrowLeft' ||
          e.key === 'ArrowRight' ||
          e.key === 'ArrowUp' ||
          e.key === 'ArrowDown')
      ) {
        return
      }
      const cmdOrCtrl = e.metaKey || e.ctrlKey

      // Cmd+R (macOS) / Ctrl+R (Windows, Linux) triggers the same
      // Build-9 capture+fetch pipeline the sidebar Refresh button runs,
      // matching the article's "the same one the sidebar button
      // triggers" promise on every platform. Uses the shared cmdOrCtrl
      // modifier so the binding fires everywhere, exactly as Cmd/Ctrl+K,
      // +F, and +G do. The pipeline context guards itself with a
      // sourceRef so a rapid double-press can't double-fire; we also
      // short-circuit on the hook's isRunning flag to avoid even calling
      // startRefresh while a refresh is in flight (defense in depth).
      // preventDefault is load-bearing: without it the browser reloads
      // the SPA (Ctrl/Cmd+R is the native reload) and the user loses
      // their place. F5 and Ctrl+Shift+R still reload by design.
      if (e.key === 'r' && cmdOrCtrl && !e.altKey && !e.shiftKey) {
        e.preventDefault()
        if (!isRefreshRunning) {
          startRefresh(true)
        }
        return
      }

      // Cmd+C: Copy selected message (detail pane only, when no text selection)
      if (e.key === 'c' && cmdOrCtrl && !e.altKey && !e.shiftKey) {
        // Only intercept if in detail pane and no text is selected (let normal copy work otherwise)
        if (focusArea === 'detail' && !window.getSelection()?.toString()) {
          const messageId = getSelectedMessageId()
          if (messageId && currentUuid) {
            const conversation = queryClient.getQueryData<ConversationDetail>(
              queryKeys.conversations.detail(currentUuid)
            )
            const message = conversation?.messages.find((m) => m.uuid === messageId)
            if (message) {
              e.preventDefault()
              const markdown = messageToMarkdown(message, showToolCalls)
              // LOW-1 (council follow-up): the surrounding handler is a
              // synchronous keydown listener (useEffect-mounted at the
              // window level), so we can't `await`. Without a `.catch()`
              // a permission/secure-origin denial surfaces as an
              // unhandled promise rejection. Architect-chosen pattern:
              // chain `.catch()` to absorb the rejection and surface
              // errorToast without altering the sync signature.
              navigator.clipboard.writeText(markdown).catch(() => {
                errorToast('Failed to copy to clipboard.')
              })
              return
            }
          }
        }
      }

      // Always allow ? for help modal
      if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (!isInputElement(e.target)) {
          e.preventDefault()
          setIsHelpOpen(true)
          return
        }
      }

      // Tab switches between panes (universal)
      // Tab only rotates between 'list' and 'detail' — never selects 'search'
      // (the search panel has its own Cmd+K trigger). If focusArea is 'search',
      // Tab jumps to 'list'.
      if (e.key === 'Tab' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (!isInputElement(e.target) && currentUuid) {
          e.preventDefault()
          if (focusArea === 'search' || focusArea === 'none') {
            setFocusArea('list')
          } else {
            setFocusArea(focusArea === 'list' ? 'detail' : 'list')
          }
          return
        }
      }

      // === SearchPanel shortcuts ===
      // These run BEFORE the blanket isInputElement guard so they still work
      // while the SearchPanel's own input is focused. The Cmd/Ctrl-modified
      // shortcuts are safe in any input; Escape is allowed through only when
      // the SearchPanel itself is open (or when the focused input opts in via
      // data-allow-shortcuts).

      // Cmd+K toggles the SearchPanel (open ↔ closed). When opening,
      // always force the right-pane tab to 'search' — Cmd+K means
      // "go to Search", not "open whatever tab was last selected".
      // The user's last-selected tab persists in preferences, so
      // without this clamp a Bookmarks-tab user gets Bookmarks back
      // on Cmd+K. (Manual finding 2026-05-14, mirrors Cmd+F's fix.)
      // Toggle-close semantics preserved: if the panel is open, just
      // close it without touching the tab state.
      if (cmdOrCtrl && e.key === 'k' && !e.altKey && !e.shiftKey) {
        e.preventDefault()
        if (!searchPanel.isOpen && rightPaneTab !== 'search') {
          setRightPaneTab('search')
        }
        searchPanel.toggle()
        return
      }

      // Cmd+F is "find" muscle memory: always open the panel (if closed)
      // AND focus the search input, even when the panel was already
      // open. Pressing Cmd+F must NEVER close the panel — that's what
      // Cmd+K and Esc are for. (Manual finding 2026-05-03.)
      // Always force the right-pane tab to 'search' — Cmd+F means find,
      // not "open whatever tab was last selected". A user who left
      // Bookmarks active should still get Search when they Cmd+F.
      if (cmdOrCtrl && e.key === 'f' && !e.altKey && !e.shiftKey) {
        e.preventDefault()
        // 2026-05-22 perf fix: skip the PATCH+rerender chain when the
        // tab is already on 'search'. setRightPaneTab → usePreferences
        // → localStorage write + PATCH /api/preferences → onSuccess
        // → setQueryData(['preferences']) → ALL useSettings consumers
        // re-render. On the 16K-msg conversation, that cascade dwarfed
        // the actual focus work (multi-second Cmd+F lag). The guard
        // makes the steady-state Cmd+F path a no-state-change focus.
        if (rightPaneTab !== 'search') setRightPaneTab('search')
        searchPanel.requestFocus()
        return
      }

      // Cmd+Shift+G: previous match (check before Cmd+G since both match 'g')
      if (cmdOrCtrl && e.key === 'g' && e.shiftKey && !e.altKey) {
        e.preventDefault()
        if (!searchPanel.isOpen) {
          searchPanel.open()
        }
        searchPanel.prevMatch()
        return
      }

      // Cmd+G: next match
      if (cmdOrCtrl && e.key === 'g' && !e.shiftKey && !e.altKey) {
        e.preventDefault()
        if (!searchPanel.isOpen) {
          searchPanel.open()
        }
        searchPanel.nextMatch()
        return
      }

      // Escape cascade: only intercept when the SearchPanel is open AND
      // focus is either outside an input or inside one that opts in
      // (the SearchPanel's own input). This preserves existing Escape
      // behaviors (detail -> list, modal dismissal) when the panel is closed.
      //
      // Manual finding 2026-05-04: Esc should close the panel and put
      // focus on the message the user landed on (the active match), so
      // they can immediately scroll/read with j/k around the hit. The
      // previous "Esc clears query, second Esc closes" two-step was
      // surprising; the user explicitly said Esc should "keep the
      // current selection".
      if (
        e.key === 'Escape' &&
        searchPanel.isOpen &&
        (!isInputElement(e.target) || allowsShortcuts(e.target))
      ) {
        e.preventDefault()
        searchPanel.close()
        // Move logical focus to the conversation pane so j/k/u/a etc
        // act on the message the user just landed on.
        setFocusArea('detail')
        // Move DOM focus to the message bubble (best-effort; the bubble
        // is rendered with tabindex=-1 elsewhere, so .focus() works).
        const selectedId = getSelectedMessageId()
        if (selectedId) {
          const el = document.querySelector(`[data-message-uuid="${selectedId}"]`)
          if (el instanceof HTMLElement) el.focus()
        }
        return
      }

      // Ignore events in input fields
      if (isInputElement(e.target)) return

      // === Universal keys (both modes) ===

      // Arrow keys
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        if (focusArea === 'detail') selectNextMessage()
        else selectNext()
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        if (focusArea === 'detail') selectPreviousMessage()
        else selectPrevious()
        return
      }
      if (e.key === 'ArrowRight' && focusArea === 'list') {
        e.preventDefault()
        const id = getSelectedId()
        if (id) {
          setNavSource('list')
          navigate(`/conversations/${id}`)
          setFocusArea('detail')
          setSelectedMessageIndex(0)
        }
        return
      }
      if (e.key === 'ArrowLeft' && focusArea === 'detail') {
        e.preventDefault()
        setFocusArea('list')
        return
      }

      // Enter: Open conversation and focus detail (from list)
      if (e.key === 'Enter' && focusArea === 'list') {
        e.preventDefault()
        const id = getSelectedId()
        if (id) {
          setNavSource('list')
          navigate(`/conversations/${id}`)
          setFocusArea('detail')
          setSelectedMessageIndex(0)
        }
        return
      }

      // Escape: Return to whichever sidebar initiated this navigation
      if (e.key === 'Escape' && focusArea === 'detail') {
        e.preventDefault()
        if (navSource === 'search') {
          if (!searchPanel.isOpen) searchPanel.open()
          setFocusArea('search')
        } else {
          setFocusArea('list')
        }
        return
      }

      // Role-based navigation (u/a/U/A) - detail only
      if (focusArea === 'detail') {
        switch (e.key) {
          case 'u':
            e.preventDefault()
            selectNextUserMessage()
            return
          case 'U':
            e.preventDefault()
            selectPreviousUserMessage()
            return
          case 'a':
            e.preventDefault()
            selectNextAssistantMessage()
            return
          case 'A':
            e.preventDefault()
            selectPreviousAssistantMessage()
            return
        }
      }

      // === Mode-specific keys ===

      if (keyboardMode === 'vim') {
        if (focusArea === 'detail') {
          switch (e.key) {
            case 'j':
              e.preventDefault()
              selectNextMessage()
              return
            case 'k':
              e.preventDefault()
              selectPreviousMessage()
              return
            case 'g':
              if (!e.ctrlKey && !e.metaKey) {
                e.preventDefault()
                selectFirstMessage()
              }
              return
            case 'G':
              e.preventDefault()
              selectLastMessage()
              return
          }
          if (e.ctrlKey) {
            switch (e.key) {
              case 'd':
                e.preventDefault()
                pageDown()
                return
              case 'u':
                e.preventDefault()
                pageUp()
                return
            }
          }
        } else {
          // List pane - vim
          switch (e.key) {
            case 'j':
              e.preventDefault()
              selectNext()
              return
            case 'k':
              e.preventDefault()
              selectPrevious()
              return
            case 'g':
              if (!e.ctrlKey && !e.metaKey) {
                e.preventDefault()
                selectFirst()
              }
              return
            case 'G':
              e.preventDefault()
              selectLast()
              return
            case '/':
              e.preventDefault()
              focusSearchInput()
              return
          }
        }
      } else {
        // Emacs mode
        if (focusArea === 'detail') {
          if (e.ctrlKey) {
            switch (e.key) {
              case 'n':
                e.preventDefault()
                selectNextMessage()
                return
              case 'p':
                e.preventDefault()
                selectPreviousMessage()
                return
            }
          }
          if (e.altKey) {
            switch (e.key) {
              case '<':
                e.preventDefault()
                selectFirstMessage()
                return
              case '>':
                e.preventDefault()
                selectLastMessage()
                return
              case 'n':
                e.preventDefault()
                pageDown()
                return
              case 'p':
                e.preventDefault()
                pageUp()
                return
            }
          }
        } else {
          // List pane - emacs
          if (e.ctrlKey) {
            switch (e.key) {
              case 'n':
                e.preventDefault()
                selectNext()
                return
              case 'p':
                e.preventDefault()
                selectPrevious()
                return
              case 's':
                e.preventDefault()
                focusSearchInput()
                return
            }
          }
          if (e.altKey) {
            switch (e.key) {
              case '<':
                e.preventDefault()
                selectFirst()
                return
              case '>':
                e.preventDefault()
                selectLast()
                return
            }
          }
        }
      }
    },
    [keyboardMode, focusArea, navSource, currentUuid, queryClient, navigate, showToolCalls,
     setFocusArea, setNavSource, setIsHelpOpen, setSelectedMessageIndex,
     selectNext, selectPrevious, selectFirst, selectLast, getSelectedId,
     selectNextMessage, selectPreviousMessage, selectFirstMessage, selectLastMessage,
     selectNextUserMessage, selectPreviousUserMessage,
     selectNextAssistantMessage, selectPreviousAssistantMessage,
     pageDown, pageUp, getSelectedMessageId, searchPanel,
     rightPaneTab, setRightPaneTab, startRefresh, isRefreshRunning]
  )

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    // A DOM signal that the shortcuts listen now, and in which mode. The
    // listener attaches after React's first commit, so a key pressed right
    // after page load can come too early; e2e tests wait for this attribute
    // before they press keys (keyboard-navigation.spec.ts, 2026-10-24).
    document.documentElement.dataset.keyboardShortcuts = keyboardMode
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      delete document.documentElement.dataset.keyboardShortcuts
    }
  }, [handleKeyDown, keyboardMode])
}