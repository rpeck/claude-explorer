import { useEffect } from 'react'
import { useNavigate } from 'react-router'
import { Sun, Moon, Monitor, Settings, Keyboard, Database, Info, ExternalLink, FileText, FolderTree } from 'lucide-react'
import { isProjectKey } from '@/lib/conversationListView'
import {
  useSettings,
  isTheme,
  isKeyboardMode,
  isMarkdownExportMode,
} from '@/contexts/SettingsContext'
import { RadioGroup } from '@/components/ui/radio-group'
import { RadioOptionCard } from '@/components/ui/RadioOptionCard'
import { useConfig, useConfigStats } from '@/hooks/useConversations'

export function SettingsPage() {
  const {
    theme,
    setTheme,
    keyboardMode,
    setKeyboardMode,
    markdownExportMode,
    setMarkdownExportMode,
    listProjectKey,
    setListProjectKey,
  } = useSettings()
  // V1 polish 2026-05-24 (Bug 2) — the previous
  // `export.includeCompactContent` pref + checkbox was REMOVED. The
  // conversation header's "Show Compactions" checkbox now drives BOTH
  // viewer visibility AND export inclusion (single source of truth).
  const navigate = useNavigate()

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        navigate(-1)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navigate])
  // /config is fast (no directory walk) and used everywhere; the slow
  // /config/stats variant populates conversation_count and is fetched
  // only here on the Settings page where the user is willing to wait.
  //
  // 2026-05-23 (perf — React Query duplicate-fetch fix): switched from
  // inline `useQuery({ queryKey: ['config'] })` to the shared
  // `useConfig()` / `useConfigStats()` hooks. The inline-vs-hook split
  // meant SettingsPage AND ConfigCorruptionBanner each subscribed to
  // ['config'] separately. React Query's queryKey-based dedup happens
  // to handle this correctly (identical key shape → shared observer),
  // but the inline form was brittle to any future rename of
  // queryKeys.config. Single source of truth via the hook eliminates
  // the foot-gun.
  //
  // Hunt #5 (2026-05-18): dropped `staleTime: Infinity` to inherit the
  // queryClient default (30s). The previous inline `Infinity` used to
  // OVERRIDE the useConfigStats hook's TTL per-observer; the hook form
  // now ensures lockstep.
  const { data: config } = useConfig()
  const { data: stats } = useConfigStats()

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl p-6">
        <header className="mb-8">
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-zinc-900 dark:text-zinc-100">
            <Settings className="h-6 w-6" />
            Settings
          </h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Customize your Claude Explorer experience
          </p>
        </header>

        <div className="space-y-8">
          {/* Theme Section */}
          <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-medium text-zinc-900 dark:text-zinc-100">
              <Sun className="h-5 w-5" />
              Appearance
            </h2>
            <div className="space-y-3">
              {/* Phase 1 a11y: group-header for the RadioGroup. <label>
                  without an associated control isn't valid; use a <div>
                  and wire it to RadioGroup via aria-labelledby so SR
                  users hear "Theme" as the group name. */}
              <div
                id="settings-theme-label"
                className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Theme
              </div>
              <RadioGroup
                value={theme}
                onValueChange={(value) => {
                  // Radix hands us a `string`; only accept values in the
                  // Theme union. Unknown values (corrupted persisted
                  // state, future Radix change) are silently rejected.
                  if (isTheme(value)) setTheme(value)
                }}
                className="grid grid-cols-3 gap-3"
                aria-labelledby="settings-theme-label"
              >
                {/* P1.3 (2026-05-30): the prior inline <label>+<RadioGroupItem>
                    triplets — each with its own oxlint-disable rationale
                    block — now live behind <RadioOptionCard>. See the
                    component for the Phase 1 a11y rationale. */}
                <RadioOptionCard
                  value="light"
                  title="Light"
                  icon={<Sun className="h-4 w-4" />}
                  active={theme === 'light'}
                  layout="inline"
                />
                <RadioOptionCard
                  value="dark"
                  title="Dark"
                  icon={<Moon className="h-4 w-4" />}
                  active={theme === 'dark'}
                  layout="inline"
                />
                <RadioOptionCard
                  value="system"
                  title="System"
                  icon={<Monitor className="h-4 w-4" />}
                  active={theme === 'system'}
                  layout="inline"
                />
              </RadioGroup>
            </div>
          </section>

          {/* Keyboard Navigation Section */}
          <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-medium text-zinc-900 dark:text-zinc-100">
              <Keyboard className="h-5 w-5" />
              Keyboard Navigation
            </h2>
            <div className="space-y-3">
              {/* Phase 1 a11y: group-header for the RadioGroup. See
                  Theme section above for rationale. */}
              <div
                id="settings-keyboard-mode-label"
                className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Keyboard Mode
              </div>
              <RadioGroup
                value={keyboardMode}
                onValueChange={(value) => {
                  if (isKeyboardMode(value)) setKeyboardMode(value)
                }}
                className="grid grid-cols-2 gap-3"
                aria-labelledby="settings-keyboard-mode-label"
              >
                <RadioOptionCard
                  value="emacs"
                  title="Emacs"
                  description="Ctrl+N/P, Ctrl+F/B, Ctrl+S"
                  active={keyboardMode === 'emacs'}
                  layout="stacked"
                />
                <RadioOptionCard
                  value="vim"
                  title="Vim"
                  description="j/k, l/h, /, gg/G"
                  active={keyboardMode === 'vim'}
                  layout="stacked"
                />
              </RadioGroup>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Press <kbd className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs dark:bg-zinc-800">?</kbd> anywhere to see all keyboard shortcuts
              </p>
            </div>
          </section>

          {/* Conversation List: what a "project" is in the list's project
              picker and its folders (PLANS/2026.10.26-list-project-key.md). */}
          <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800" data-section="conversation-list">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-medium text-zinc-900 dark:text-zinc-100">
              <FolderTree className="h-5 w-5" />
              Conversation List
            </h2>
            <div className="space-y-3">
              <div
                id="settings-project-key-label"
                className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Projects are
              </div>
              <RadioGroup
                value={listProjectKey}
                onValueChange={(value) => {
                  if (isProjectKey(value)) setListProjectKey(value)
                }}
                className="grid grid-cols-2 gap-3"
                aria-labelledby="settings-project-key-label"
              >
                <RadioOptionCard
                  value="path"
                  id="settings-project-key-path-radio"
                  title="Folder path"
                  description="Each folder is its own project."
                  active={listProjectKey === 'path'}
                  layout="stacked"
                  testId="settings-project-key-path"
                />
                <RadioOptionCard
                  value="name"
                  id="settings-project-key-name-radio"
                  title="Folder name"
                  description="Folders with the same name are one project."
                  active={listProjectKey === 'name'}
                  layout="stacked"
                  testId="settings-project-key-name"
                />
              </RadioGroup>
            </div>
          </section>

          {/* Export Section (Markdown + PDF) */}
          {/* Unified 2026-05-29: a single tri-state radio binds to
              `markdownExportMode`, the canonical key the conversation
              header's Markdown dialog also reads/writes. Previously a
              boolean checkbox + 2-radio dialect group wrote to orphan
              keys (`markdownBundleImages` + `markdownDialect`) that the
              dialog and exporter ignored — so the user's Settings choice
              never reached the export. */}
          <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800" data-section="markdown-export">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-medium text-zinc-900 dark:text-zinc-100">
              <FileText className="h-5 w-5" />
              Export
            </h2>
            <div className="space-y-3">
              <div
                id="settings-markdown-export-mode-label"
                className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Default Markdown export mode
              </div>
              <p className="mt-0.5 mb-2 text-xs text-zinc-500 dark:text-zinc-400">
                Sets the default radio in the Markdown export dialog. Inline produces a single <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[11px] dark:bg-zinc-800">.md</code> file. Bundle CommonMark and Bundle Obsidian produce a zip with <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[11px] dark:bg-zinc-800">conversation.md</code> + <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[11px] dark:bg-zinc-800">images/</code> + <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[11px] dark:bg-zinc-800">attachments/</code>; Obsidian uses wikilink syntax for inline previews in Obsidian.
              </p>
              <RadioGroup
                value={markdownExportMode}
                onValueChange={(value) => {
                  if (isMarkdownExportMode(value)) setMarkdownExportMode(value)
                }}
                className="space-y-2"
                aria-labelledby="settings-markdown-export-mode-label"
              >
                <RadioOptionCard
                  value="inline"
                  id="settings-md-mode-inline"
                  title="Inline"
                  description="Single .md file. Images embedded inline or omitted."
                  active={markdownExportMode === 'inline'}
                  layout="stacked"
                />
                <RadioOptionCard
                  value="bundle-commonmark"
                  id="settings-md-mode-bundle-cm"
                  title="Bundle CommonMark"
                  description={
                    <>
                      Zip with conversation.md, images/, attachments/. Standard Markdown links (
                      <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[11px] dark:bg-zinc-800">
                        ![alt](images/x.png)
                      </code>
                      ).
                    </>
                  }
                  active={markdownExportMode === 'bundle-commonmark'}
                  layout="stacked"
                />
                <RadioOptionCard
                  value="bundle-obsidian"
                  id="settings-md-mode-bundle-ob"
                  title="Bundle Obsidian"
                  description={
                    <>
                      Same as CommonMark but uses Obsidian wikilinks (
                      <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[11px] dark:bg-zinc-800">
                        ![[images/x.png]]
                      </code>
                      ).
                    </>
                  }
                  active={markdownExportMode === 'bundle-obsidian'}
                  layout="stacked"
                />
              </RadioGroup>
            </div>
          </section>

          {/* Data Section */}
          <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-medium text-zinc-900 dark:text-zinc-100">
              <Database className="h-5 w-5" />
              Data
            </h2>
            <div className="space-y-3">
              <div>
                {/* Phase 1 a11y: not a form control header; <label> with
                    no associated control is invalid. Render as <div>. */}
                <div className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                  Data Directory
                </div>
                <p className="mt-1 font-mono text-sm text-zinc-600 dark:text-zinc-400">
                  {config?.data_dir || 'Loading...'}
                </p>
              </div>
              <div>
                <div className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                  Total Conversations
                </div>
                <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                  {stats?.conversation_count !== undefined ? stats.conversation_count.toLocaleString() : 'Loading...'}
                </p>
              </div>
            </div>
          </section>

          {/* About Section */}
          <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-medium text-zinc-900 dark:text-zinc-100">
              <Info className="h-5 w-5" />
              About
            </h2>
            <div className="space-y-2 text-sm text-zinc-600 dark:text-zinc-400">
              <p>
                <strong className="text-zinc-900 dark:text-zinc-100">Claude Explorer</strong> - Browse and export your Claude conversations
              </p>
              {config?.version && (
                <p data-testid="about-version">Version {config.version}</p>
              )}
              <a
                href="https://github.com/rpeck/claude-explorer"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-blue-600 hover:underline dark:text-blue-400"
              >
                View on GitHub
                <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
