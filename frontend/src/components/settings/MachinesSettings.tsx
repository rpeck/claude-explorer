/**
 * Issue #92: the Machines settings. UX.md → "Machines".
 *
 * One archive can hold the sessions of several machines. This section sets
 * this machine's label and local names for the others, the export rules
 * (nothing leaves by default), bundles, and the shared sync folder.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { api } from '@/lib/api'
import { ApiError, type ExportMode, type ExportRules, type MachineInfo } from '@/lib/types'
import { machinesKeys, useMachines } from '@/components/settings/machinesQuery'
import { MODES, formatAllow, formatBytes, parseAllow, shortPath } from '@/components/settings/machinesFormat'

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const detail = JSON.parse(error.message)?.detail
      if (typeof detail === 'string' && detail) return detail
      if (detail && typeof detail.message === 'string') return detail.message
      if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
    } catch {
      // not JSON; fall through to the raw text
    }
    return error.message || `HTTP ${error.status}`
  }
  return error instanceof Error ? error.message : String(error)
}

function ownLabelValue(info: MachineInfo | undefined): string {
  if (!info) return ''
  // The default label is "Machine <id8>"; the input then starts empty.
  return info.label === `Machine ${info.machine_id.slice(0, 8)}` ? '' : info.label
}

function MachineRow({ info, onSaved }: { info: MachineInfo; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(info.label)
  const [error, setError] = useState<string | null>(null)
  const save = async () => {
    setError(null)
    try {
      await api.setMachineLocalLabel(info.machine_id, name.trim() || null)
      setEditing(false)
      onSaved()
    } catch (e) {
      setError(errorText(e))
    }
  }
  return (
    <li data-testid={`machine-row-${info.machine_id}`} className="flex flex-wrap items-center gap-2 py-1 text-sm">
      {editing ? (
        <>
          <Input
            data-testid={`machine-rename-input-${info.machine_id}`}
            aria-label={`Local name for ${info.label}`}
            value={name}
            maxLength={64}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void save()
              if (e.key === 'Escape') setEditing(false)
            }}
            className="h-7 w-48"
          />
          <Button size="sm" data-testid={`machine-rename-save-${info.machine_id}`} onClick={() => void save()}>
            Save
          </Button>
        </>
      ) : (
        <span className="font-medium text-zinc-900 dark:text-zinc-100">{info.label}</span>
      )}
      {info.is_this_machine && <span className="text-xs text-zinc-500">This machine</span>}
      <span className="text-xs text-zinc-500">
        {info.session_count} {info.session_count === 1 ? 'session' : 'sessions'}
      </span>
      {!info.is_this_machine && !editing && (
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          data-testid={`machine-rename-${info.machine_id}`}
          title="Give this machine a local name (it stays on this machine)"
          aria-label={`Rename ${info.label} on this machine`}
          onClick={() => {
            setName(info.label)
            setEditing(true)
          }}
        >
          <Pencil className="h-3 w-3" />
        </Button>
      )}
      {error && <span role="alert" className="text-xs text-red-600">{error}</span>}
    </li>
  )
}

function RulesForm({ rules, onSaved }: { rules: ExportRules; onSaved: () => void }) {
  // The radio shows a click at once; a failed save puts the saved mode back.
  const [mode, setMode] = useState<ExportMode>(rules.mode)
  const [allow, setAllow] = useState(formatAllow(rules.allow))
  const [deny, setDeny] = useState(rules.deny.join('\n'))
  const [since, setSince] = useState(rules.since ?? '')
  const [error, setError] = useState<string | null>(null)

  const save = async (next: ExportMode) => {
    setError(null)
    setMode(next)
    try {
      await api.saveExportRules({
        version: 1,
        mode: next,
        allow: parseAllow(allow),
        deny: deny.split('\n').map((d) => d.trim()).filter(Boolean),
        since: since || null,
      })
      onSaved()
    } catch (e) {
      setMode(rules.mode)
      setError(errorText(e))
    }
  }

  return (
    <div className="min-w-0 space-y-2">
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Export mode</legend>
        {MODES.map((m) => (
          <label key={m.value} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="export-mode"
              data-testid={`export-mode-${m.value}`}
              checked={mode === m.value}
              onChange={() => void save(m.value)}
            />
            {m.label}
          </label>
        ))}
      </fieldset>
      <label className="block text-sm">
        <span className="font-medium">Allow</span>
        <span className="block text-xs text-zinc-500">
          One rule per line: a source (claude-code, cowork, codex, or *) and a project glob, for example
          "claude-code ~/Source/personal/**".
        </span>
        <textarea
          data-testid="export-rules-allow"
          className="mt-1 w-full rounded border border-zinc-300 bg-transparent p-2 font-mono text-xs dark:border-zinc-700"
          rows={3}
          value={allow}
          onChange={(e) => setAllow(e.target.value)}
        />
      </label>
      <label className="block text-sm">
        <span className="font-medium">Deny</span>
        <span className="block text-xs text-zinc-500">One project glob per line. A deny always wins.</span>
        <textarea
          data-testid="export-rules-deny"
          className="mt-1 w-full rounded border border-zinc-300 bg-transparent p-2 font-mono text-xs dark:border-zinc-700"
          rows={2}
          value={deny}
          onChange={(e) => setDeny(e.target.value)}
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <span className="font-medium">Since</span>
        <Input
          type="date"
          data-testid="export-rules-since"
          className="h-8 w-44"
          value={since}
          onChange={(e) => setSince(e.target.value)}
        />
      </label>
      <Button size="sm" data-testid="export-rules-save" onClick={() => void save(mode)}>
        Save rules
      </Button>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    </div>
  )
}

export function MachinesSettings() {
  const queryClient = useQueryClient()
  const machines = useMachines()
  const rules = useQuery({ queryKey: machinesKeys.rules, queryFn: ({ signal }) => api.getExportRules(signal) })
  const preview = useQuery({
    queryKey: machinesKeys.preview,
    queryFn: ({ signal }) => api.getExportPreview(signal),
  })
  const own = machines.data?.machines.find((m) => m.is_this_machine)
  const [label, setLabel] = useState<string | null>(null)
  const [passphrase, setPassphrase] = useState('')
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importPassphrase, setImportPassphrase] = useState('')
  const [importResult, setImportResult] = useState<string | null>(null)
  const [shared, setShared] = useState<string | null>(null)
  const [removeOpen, setRemoveOpen] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: machinesKeys.all })
    void queryClient.invalidateQueries({ queryKey: ['conversations'] })
  }
  const run = async (action: () => Promise<string | void>) => {
    setBusy(true)
    setMessage(null)
    try {
      const text = await action()
      if (text) setMessage(text)
    } catch (e) {
      setMessage(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const labelValue = label ?? ownLabelValue(own)
  const sharedValue = shared ?? machines.data?.shared_archive_dir ?? ''
  const block = machines.data?.shared_archive_block
  const mode = rules.data?.mode ?? 'off'

  const doExport = () =>
    run(async () => {
      const { blob, filename } = await api.exportBundle(passphrase || null)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      void queryClient.invalidateQueries({ queryKey: machinesKeys.preview })
      return `Saved ${filename}`
    })

  const doImport = async () => {
    if (!importFile) return
    setBusy(true)
    try {
      const result = await api.importBundle(importFile, importPassphrase || null)
      const counts = Object.entries(result.counts).map(([k, n]) => `${n} ${k}`).join(', ')
      const from = result.label || `Machine ${result.machine_id.slice(0, 8)}`
      setImportResult([`Imported from ${from}: ${counts}`, ...result.warnings].join('\n'))
      refresh()
    } catch (e) {
      setImportResult(`Import refused: ${errorText(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section data-testid="machines-settings" className="min-w-0 space-y-5">
      <h2 className="text-lg font-semibold">Machines</h2>

      <div className="space-y-1">
        <label className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">This machine&apos;s label</span>
          <Input
            data-testid="machine-label-input"
            className="h-8 w-56"
            maxLength={64}
            placeholder={own?.label}
            value={labelValue}
            onChange={(e) => setLabel(e.target.value)}
          />
          <Button
            size="sm"
            data-testid="machine-label-save"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.setOwnMachineLabel(labelValue.trim() || null)
                setLabel(null)
                refresh()
              })
            }
          >
            Save
          </Button>
        </label>
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {machines.data?.machines.map((m) => (
            <MachineRow key={`${m.machine_id}:${m.label}`} info={m} onSaved={refresh} />
          ))}
        </ul>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold">What leaves this machine</h3>
        {rules.data && (
          <RulesForm
            key={JSON.stringify(rules.data)}
            rules={rules.data}
            onSaved={() => {
              void queryClient.invalidateQueries({ queryKey: machinesKeys.rules })
              void queryClient.invalidateQueries({ queryKey: machinesKeys.preview })
            }}
          />
        )}
        <div data-testid="export-preview" className="max-h-40 overflow-y-auto overflow-x-hidden rounded border border-zinc-200 p-2 text-xs dark:border-zinc-800">
          <p data-testid="export-preview-total" className="font-medium">
            {preview.data ? `${preview.data.file_count} files, ${formatBytes(preview.data.total_bytes)}` : '…'}
          </p>
          <ul className="font-mono">
            {preview.data?.files.map((f) => (
              <li key={f.path} className="truncate whitespace-nowrap" title={f.path}>
                {shortPath(f.path)} ({formatBytes(f.bytes)})
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Bundles</h3>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            type="password"
            data-testid="export-passphrase"
            className="h-8 w-56"
            autoComplete="new-password"
            placeholder="Passphrase (empty = not encrypted)"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
          />
          <Button size="sm" data-testid="export-button" disabled={busy || mode !== 'export'} onClick={() => void doExport()}>
            Export bundle
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="file"
            data-testid="import-file"
            aria-label="Bundle file"
            className="text-xs"
            onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
          />
          <Input
            type="password"
            data-testid="import-passphrase"
            className="h-8 w-44"
            autoComplete="off"
            placeholder="Passphrase"
            value={importPassphrase}
            onChange={(e) => setImportPassphrase(e.target.value)}
          />
          <Button size="sm" data-testid="import-button" disabled={busy || !importFile} onClick={() => void doImport()}>
            Import bundle
          </Button>
        </div>
        {importResult && (
          <p data-testid="import-result" className="whitespace-pre-wrap text-xs">{importResult}</p>
        )}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Shared sync folder</h3>
        <p className="text-xs text-zinc-500">
          Each machine writes only its own folder here, and copies the other machines&apos; folders into this archive.
          The search index never goes here.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            data-testid="shared-folder-input"
            className="h-8 w-80 max-w-full font-mono text-xs"
            placeholder="/absolute/path/to/shared/folder"
            value={sharedValue}
            onChange={(e) => setShared(e.target.value)}
          />
          <Button
            size="sm"
            data-testid="shared-folder-save"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.setSharedFolder(sharedValue.trim() || null)
                setShared(null)
                refresh()
              })
            }
          >
            Save
          </Button>
          <Button
            size="sm"
            variant="outline"
            data-testid="shared-folder-remove"
            disabled={busy || !machines.data?.shared_archive_dir}
            onClick={() => setRemoveOpen(true)}
          >
            Remove this machine&apos;s folder
          </Button>
        </div>
        {block && machines.data?.shared_archive_dir && (
          <div className="space-y-1 text-xs text-amber-700 dark:text-amber-400">
            <p>
              {block.provider} syncs this folder to its cloud. Claude Explorer writes nothing there until you allow it.
            </p>
            <Button
              size="sm"
              variant="destructive"
              data-testid="shared-folder-allow"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.allowSharedArchiveWrites({
                    shared_archive_dir: machines.data?.shared_archive_dir ?? '',
                    provider: block.provider,
                  })
                  refresh()
                })
              }
            >
              Allow writes in this {block.provider} folder
            </Button>
          </div>
        )}
      </div>

      {message && <p role="status" className="text-sm">{message}</p>}

      <Dialog open={removeOpen} onOpenChange={setRemoveOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove this machine&apos;s folder?</DialogTitle>
            <DialogDescription>
              Claude Explorer deletes only this machine&apos;s folder in the shared folder. What other machines already
              copied stays with them. If the export mode stays &quot;Export&quot;, the next pass sends the files again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRemoveOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              data-testid="shared-folder-remove-confirm"
              onClick={() =>
                void run(async () => {
                  await api.removeOwnSharedFolder(machines.data?.this_machine_id ?? '')
                  setRemoveOpen(false)
                  return 'Removed this machine\'s folder from the shared folder.'
                })
              }
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

export function MachinesDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl" data-testid="machines-dialog">
        <DialogHeader className="sr-only">
          <DialogTitle>Machines</DialogTitle>
          <DialogDescription>Labels, export rules, bundles, and the shared sync folder.</DialogDescription>
        </DialogHeader>
        {open && <MachinesSettings />}
      </DialogContent>
    </Dialog>
  )
}
