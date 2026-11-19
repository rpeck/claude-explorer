import { useState } from 'react'
import { CloudOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CloudSyncConsentDialog } from '@/components/settings/CloudSyncConsentDialog'
import { useConfig } from '@/hooks/useConversations'

/**
 * Plan D13, §4.3: a persistent banner while the cloud-sync gate is closed.
 * Contract: UX.md → "Cloud-sync gate".
 *
 * It is not dismissible, like ConfigCorruptionBanner: while the gate is
 * closed, Refresh and the other writers refuse, and the banner is the
 * explanation. Its button opens the consent dialog. It shows also for a
 * user whose data was in a cloud folder before the upgrade.
 */
export function CloudSyncGateBanner() {
  const { data } = useConfig()
  const gate = data?.cloud_sync_gate
  const [dialogOpen, setDialogOpen] = useState(false)

  if (!gate) return null

  return (
    <div
      data-testid="cloud-sync-gate-banner"
      role="alert"
      className="border-b border-amber-300 bg-amber-50 px-4 py-3 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
    >
      <div className="flex items-start gap-3">
        <CloudOff
          className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400"
          aria-hidden="true"
        />
        <div className="flex-1 text-sm">
          <p className="font-medium">
            Claude Explorer does not write to your data folder, because {gate.provider} syncs it to
            the cloud.
          </p>
          <p className="mt-1 break-words text-xs opacity-80">
            Folder: <code className="font-mono">{gate.folder}</code>
          </p>
          <p className="mt-1 text-xs">
            Until you allow the writes, Refresh does not run, search is slower, and settings stay
            only in this browser. New Claude Code images wait in a local folder. The data that is
            already there stays.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => setDialogOpen(true)}>
          Allow writes…
        </Button>
      </div>
      <CloudSyncConsentDialog open={dialogOpen} onOpenChange={setDialogOpen} gate={gate} />
    </div>
  )
}
