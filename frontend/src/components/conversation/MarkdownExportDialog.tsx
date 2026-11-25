/**
 * MarkdownExportDialog (Phase 7) — pop a chooser dialog when the user
 * clicks the "Markdown" button on the conversation header.
 *
 * Three modes:
 *   - inline:            single .md file (no zip)
 *   - bundle-commonmark: zip with images/ + attachments/ + conversation.md
 *                        using CommonMark links
 *   - bundle-obsidian:   same shape as commonmark but with Obsidian
 *                        wikilink syntax
 *
 * The user's last selected mode is stored under the
 * `markdownExportMode` key (canonical, shared with Settings → Export
 * since unification on 2026-05-29). The dialog reads/writes through
 * SettingsContext so changes here update the Settings page in lockstep
 * and vice versa. An optional "Save as default" checkbox writes the
 * choice through `setMarkdownExportMode` when the user clicks Download;
 * without the checkbox the choice is local to this dialog session only.
 *
 * The PDF button on the header is intentionally NOT routed through
 * this dialog — it stays a single-click direct download.
 */

import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { RadioGroup } from '@/components/ui/radio-group'
import { RadioOptionCard } from '@/components/ui/RadioOptionCard'
import { CheckboxRow } from '@/components/ui/CheckboxRow'
import {
  useSettings,
  isMarkdownExportMode,
  type MarkdownExportMode,
} from '@/contexts/SettingsContext'
import { api } from '@/lib/api'
import { downloadBlob, sanitizeFilename } from '@/lib/utils'

interface MarkdownExportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversationUuid: string
  conversationName: string
}

export function MarkdownExportDialog({
  open,
  onOpenChange,
  conversationUuid,
  conversationName,
}: MarkdownExportDialogProps) {
  const {
    showToolCalls,
    showCodeMode,
    hideCompactMarkers,
    markdownExportMode: storedMode,
    setMarkdownExportMode: setStoredMode,
  } = useSettings()
  // V1 polish 2026-05-24 (Bug 2) — unified toggle: the conversation
  // header's "Show Compactions" checkbox is the SINGLE source of truth
  // for whether compactions are visible in the viewer AND included in
  // exports. The previous `export.includeCompactContent` Settings pref
  // is removed. Mapping: includeCompact = !hideCompactMarkers (the
  // pref's name is the negative, the user-facing label is the positive).
  const includeCompact = !hideCompactMarkers

  // Local copy of the radio selection so the user can audition a mode
  // (without persisting) before clicking Download. We seed from the
  // stored preference each time the dialog opens so reopening reflects
  // the current pre-selection.
  const [mode, setMode] = useState<MarkdownExportMode>(storedMode)
  const [saveAsDefault, setSaveAsDefault] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      // react-doctor-disable-next-line react-doctor/no-adjust-state-on-prop-change -- Phase 2: deliberate "seed-on-open" UX. The user audits a mode without persisting; reopening must re-seed from storedMode. The rule's "use a key prop" recommendation would remount the entire Radix Dialog on every open, breaking focus-trap timing, animation, and the controlled-open pattern.
      setMode(storedMode) // eslint-disable-line react-hooks/set-state-in-effect -- Deliberate seed-on-open UX: re-seed audition state from storedMode each time the dialog opens (see doctor-disable rationale above). A key prop would break Radix Dialog focus trap + animation.
      // react-doctor-disable-next-line react-doctor/no-adjust-state-on-prop-change -- Phase 2: same seed-on-open rationale. (ESLint react-hooks/set-state-in-effect only flags the first setState in an effect; the disable above setMode already covers this one.)
      setSaveAsDefault(false)
    }
  }, [open, storedMode])

  const handleDownload = async () => {
    if (busy) return
    setBusy(true)
    try {
      if (saveAsDefault) {
        setStoredMode(mode)
      }
      const safeName = sanitizeFilename(conversationName || 'conversation')
      if (mode === 'inline') {
        const response = await api.exportMarkdown(
          conversationUuid,
          showToolCalls,
          includeCompact,
          showCodeMode,
        )
        const blob = await response.blob()
        downloadBlob(blob, `${safeName}.md`)
      } else {
        const dialect = mode === 'bundle-obsidian' ? 'obsidian' : 'commonmark'
        const response = await api.exportMarkdownBundle(
          conversationUuid,
          showToolCalls,
          dialect,
          includeCompact,
          showCodeMode,
        )
        const blob = await response.blob()
        downloadBlob(blob, `${safeName}.zip`)
      }
      onOpenChange(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="markdown-export-dialog">
        <DialogHeader>
          <DialogTitle>Markdown export</DialogTitle>
          <DialogDescription>
            Choose how to export this conversation as Markdown.
          </DialogDescription>
        </DialogHeader>

        <RadioGroup
          value={mode}
          onValueChange={(v) => {
            if (isMarkdownExportMode(v)) setMode(v)
          }}
          className="gap-3"
        >
          {/* P1.3 (2026-05-30): three card-shaped <label>+<RadioGroupItem>
              pairs collapsed into <RadioOptionCard>. The dialog mirrors
              the Settings page Export Mode shape; selection visuals are
              delegated to the surrounding RadioGroup's `value` prop, so
              we pass `active={mode === ...}` to keep the border swap. */}
          <RadioOptionCard
            value="inline"
            id="md-mode-inline"
            title="Inline"
            description="Single .md file. Images embedded inline or omitted."
            active={mode === 'inline'}
            layout="stacked"
          />
          <RadioOptionCard
            value="bundle-commonmark"
            id="md-mode-bundle-cm"
            title="Bundle CommonMark"
            description="Zip with conversation.md, images/, attachments/. Standard Markdown links."
            active={mode === 'bundle-commonmark'}
            layout="stacked"
          />
          <RadioOptionCard
            value="bundle-obsidian"
            id="md-mode-bundle-ob"
            title="Bundle Obsidian"
            description="Same as CommonMark but uses Obsidian wikilink syntax."
            active={mode === 'bundle-obsidian'}
            layout="stacked"
          />
        </RadioGroup>

        <CheckboxRow
          label="Save as default"
          checked={saveAsDefault}
          onCheckedChange={setSaveAsDefault}
        />

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button onClick={handleDownload} disabled={busy}>
            Download
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
