/**
 * Issue #92: the sidebar's "Machines" button. It opens the Machines settings.
 * Its own state keeps the Sidebar change to one line.
 */
import { useState } from 'react'
import { Laptop } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { MachinesDialog } from '@/components/settings/MachinesSettings'

export function MachinesButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        title="Machines"
        aria-label="Machines"
        data-testid="machines-button"
        onClick={() => setOpen(true)}
      >
        <Laptop className="h-4 w-4" />
      </Button>
      <MachinesDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
