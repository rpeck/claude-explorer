/**
 * CloudSyncConsentDialog — the user's opt-in to writes in a cloud-synced
 * data folder (plan D13, §4.3). Contract: UX.md → "Cloud-sync gate".
 *
 * Three steps, so that work transcripts never reach a cloud service by
 * one stray click:
 *   1. a warning that names the provider and the folder;
 *   2. a check box that the user must tick;
 *   3. the provider name, typed by the user.
 * Only step 3 calls the route. The route compares `data_dir` and the
 * typed provider with its detection now (case-insensitive), so the
 * dialog sends back the `data_dir` of the gate that it showed.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { CheckboxRow } from '@/components/ui/CheckboxRow'
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
import { queryKeys } from '@/lib/queryClient'
import { ApiError, type CloudSyncGate } from '@/lib/types'

interface CloudSyncConsentDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  gate: CloudSyncGate
}

function reasonFor(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const detail = JSON.parse(error.message)?.detail
      if (typeof detail === 'string' && detail) return detail
    } catch {
      // not JSON; fall through to the raw text
    }
    return error.message || `HTTP ${error.status}`
  }
  return error instanceof Error ? error.message : String(error)
}

export function CloudSyncConsentDialog({ open, onOpenChange, gate }: CloudSyncConsentDialogProps) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [understood, setUnderstood] = useState(false)
  const [typed, setTyped] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const typedMatches = typed.trim().toLowerCase() === gate.provider.toLowerCase()

  const reset = () => {
    setStep(1)
    setUnderstood(false)
    setTyped('')
    setError(null)
  }

  // Every open starts at step 1, also when the parent closed the dialog.
  // Adjusted during render, so a reopened dialog never shows an old step.
  const [wasOpen, setWasOpen] = useState(open)
  if (wasOpen !== open) {
    setWasOpen(open)
    if (open) reset()
  }

  const changeOpen = (next: boolean) => {
    if (saving) return
    onOpenChange(next)
  }

  const allow = async () => {
    setSaving(true)
    setError(null)
    try {
      await api.allowCloudSyncWrites({ data_dir: gate.data_dir, provider: typed.trim() })
    } catch (e) {
      setError(reasonFor(e))
      setSaving(false)
      return
    }
    setSaving(false)
    // The banner reads /api/config; a refetch shows the open gate.
    void queryClient.invalidateQueries({ queryKey: queryKeys.config })
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent className="sm:max-w-lg" data-testid="cloud-sync-consent-dialog">
        <DialogHeader>
          <DialogTitle>Allow writes in a {gate.provider} folder?</DialogTitle>
          <DialogDescription>Step {step} of 3</DialogDescription>
        </DialogHeader>

        {step === 1 && (
          <div className="space-y-2 text-sm text-zinc-700 dark:text-zinc-300">
            <p>
              Your Claude Explorer data folder is synced by <strong>{gate.provider}</strong>:
            </p>
            <p className="break-all font-mono text-xs">{gate.folder}</p>
            <p>
              If you allow writes, Claude Explorer saves your conversations, the search index, and
              images in this folder, and {gate.provider} uploads them to its cloud. Your
              conversations can contain work data.
            </p>
          </div>
        )}

        {step === 2 && (
          <CheckboxRow
            checked={understood}
            onCheckedChange={setUnderstood}
            label={`I understand that ${gate.provider} uploads my conversation data to its cloud.`}
          />
        )}

        {step === 3 && (
          <label className="block space-y-1 text-sm text-zinc-700 dark:text-zinc-300">
            <span>
              Type <strong>{gate.provider}</strong> to allow the writes.
            </span>
            <Input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={saving}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        )}

        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            Could not allow the writes. {error}
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => changeOpen(false)} disabled={saving}>
            Cancel
          </Button>
          {step === 1 && <Button onClick={() => setStep(2)}>Continue</Button>}
          {step === 2 && (
            <Button onClick={() => setStep(3)} disabled={!understood}>
              Continue
            </Button>
          )}
          {step === 3 && (
            <Button variant="destructive" onClick={allow} disabled={!typedMatches || saving}>
              {saving ? 'Allowing…' : 'Allow writes'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
