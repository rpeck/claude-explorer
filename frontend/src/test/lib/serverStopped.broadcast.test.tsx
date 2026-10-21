/**
 * UX.md → "Quit (power button)", "Other tabs": after Quit in one tab, every
 * other open tab shows the same "Claude Explorer has stopped" screen
 * instead of the "Connecting to Backend" / "Connection Failed" dialog.
 *
 * jsdom has no cross-tab transport, so a small in-memory BroadcastChannel
 * stands in for the browser's: a message posted on one instance reaches
 * every OTHER open instance with the same name, never the sender.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'

type Listener = (event: MessageEvent) => void

class FakeBroadcastChannel {
  static open: FakeBroadcastChannel[] = []
  name: string
  onmessage: Listener | null = null
  closed = false

  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.open.push(this)
  }

  postMessage(data: unknown): void {
    for (const other of FakeBroadcastChannel.open) {
      if (other !== this && !other.closed && other.name === this.name) {
        other.onmessage?.({ data } as MessageEvent)
      }
    }
  }

  close(): void {
    this.closed = true
  }
}

/** A second tab: a channel opened outside the module under test. */
function otherTab(name: string): { posted: unknown[]; channel: FakeBroadcastChannel } {
  const channel = new FakeBroadcastChannel(name)
  const posted: unknown[] = []
  channel.onmessage = (e) => posted.push(e.data)
  return { posted, channel }
}

async function freshModule() {
  vi.resetModules()
  return import('../../lib/serverStopped')
}

describe('server-stopped flag is shared across tabs', () => {
  beforeEach(() => {
    FakeBroadcastChannel.open = []
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('markServerStopped tells the other tabs', async () => {
    const mod = await freshModule()
    function Probe() {
      return <span>{mod.useServerStopped() ? 'stopped' : 'running'}</span>
    }
    render(<Probe />)
    expect(FakeBroadcastChannel.open.length).toBe(1)
    const tab = otherTab(FakeBroadcastChannel.open[0].name)

    act(() => mod.markServerStopped())

    expect(screen.getByText('stopped')).toBeInTheDocument()
    expect(tab.posted).toEqual([{ type: 'server-stopped' }])
  })

  it('a stop message from another tab marks this tab stopped', async () => {
    const mod = await freshModule()
    function Probe() {
      return <span>{mod.useServerStopped() ? 'stopped' : 'running'}</span>
    }
    render(<Probe />)
    expect(screen.getByText('running')).toBeInTheDocument()
    const tab = otherTab(FakeBroadcastChannel.open[0].name)

    act(() => tab.channel.postMessage({ type: 'server-stopped' }))

    expect(screen.getByText('stopped')).toBeInTheDocument()
    expect(mod.isServerStopped()).toBe(true)
  })

  it('ignores unrelated messages on the channel', async () => {
    const mod = await freshModule()
    function Probe() {
      return <span>{mod.useServerStopped() ? 'stopped' : 'running'}</span>
    }
    render(<Probe />)
    const tab = otherTab(FakeBroadcastChannel.open[0].name)

    act(() => tab.channel.postMessage({ type: 'something-else' }))
    act(() => tab.channel.postMessage('server-stopped'))

    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('works without BroadcastChannel (older browsers): local flag only', async () => {
    vi.stubGlobal('BroadcastChannel', undefined)
    const mod = await freshModule()
    function Probe() {
      return <span>{mod.useServerStopped() ? 'stopped' : 'running'}</span>
    }
    render(<Probe />)
    act(() => mod.markServerStopped())
    expect(screen.getByText('stopped')).toBeInTheDocument()
  })
})
