import { useEffect, useState } from 'react'
import { Outlet } from 'react-router'
import { Menu, X } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { SearchPanel } from '@/components/search/SearchPanel'
import { ConfigCorruptionBanner } from '@/components/ConfigCorruptionBanner'
import { WatcherMissingBanner } from '@/components/WatcherMissingBanner'
import { CloudSyncGateBanner } from './CloudSyncGateBanner'
import { Button } from '@/components/ui/button'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { cn } from '@/lib/utils'

export function RootLayout() {
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Close drawer on viewport returning to desktop.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- TODO React 19 migration: derive drawerOpen from (drawerOpen && isMobile). Today this is event-driven via the resize external system; cascade is one render-pass.
    if (!isMobile) setDrawerOpen(false)
  }, [isMobile])

  return (
    <div className="flex h-screen flex-col bg-white dark:bg-zinc-950">
      {/* Layer 3 of PLANS/2026.05.18-config-corruption-safe-mode.md:
          renders at the very top of the app shell so the corruption
          warning is the first thing the user sees. Renders nothing
          when config.json parses cleanly — flex container shrinks
          accordingly with no layout jump. */}
      <ConfigCorruptionBanner />
      {/* Plan D13: renders nothing while the cloud-sync gate is open. */}
      <CloudSyncGateBanner />
      {/* PLANS/2026.05.26-watcher-install-detection.md Phase 3.
          Renders nothing when the supervised watcher is installed
          (the common case) — flex container shrinks accordingly, no
          layout jump. Stacks below the corruption banner because the
          corruption case is more urgent (writes are 503'd) than a
          missing-watcher case (only future image-cache rotations are
          at risk). */}
      <WatcherMissingBanner />
      <div className="flex flex-1 min-h-0">
      {!isMobile && <Sidebar />}

      {isMobile && (
        <>
          {drawerOpen && (
            <div
              className="fixed inset-0 z-30 bg-black/40"
              onClick={() => setDrawerOpen(false)}
              aria-hidden
            />
          )}
          <div
            className={cn(
              'fixed left-0 top-0 z-40 h-full transition-transform duration-200',
              drawerOpen ? 'translate-x-0' : '-translate-x-full'
            )}
          >
            <Sidebar />
            <Button
              variant="ghost"
              size="icon"
              className="absolute right-2 top-2 h-8 w-8 bg-white/90 dark:bg-zinc-900/90"
              onClick={() => setDrawerOpen(false)}
              aria-label="Close sidebar"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        </>
      )}

      <main className={cn('flex-1 overflow-hidden', isMobile && 'w-screen')}>
        {isMobile && !drawerOpen && (
          <div className="flex items-center border-b border-zinc-200 px-2 py-2 dark:border-zinc-800">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open sidebar"
              className="h-8 w-8"
            >
              <Menu className="h-4 w-4" />
            </Button>
          </div>
        )}
        <Outlet />
      </main>
      <SearchPanel />
      </div>
    </div>
  )
}
