import { describe, expect, it } from 'vitest'
import { render, screen } from '../utils'
import { MessageBubble } from '../../components/message/MessageBubble'
import { CM_MESSAGES } from '../fixtures/codeMode'

/**
 * Issue #90: a bubble shows its Code Mode run only with Show Code Mode on.
 * The prop is in the memo comparator, so a flip re-renders the bubble.
 */
const runMessage = CM_MESSAGES[1]

describe('MessageBubble and Show Code Mode', () => {
  it('renders nothing for a run-only message while the toggle is off, also with Show Tools on', () => {
    const { container } = render(<MessageBubble message={runMessage} showToolCalls />)
    expect(container.querySelector('[data-testid="code-run"]')).toBeNull()
    expect(container.querySelector('[data-message-uuid]')).toBeNull()
  })

  it('renders the run when the toggle turns on', () => {
    const { rerender } = render(<MessageBubble message={runMessage} />)
    expect(screen.queryByTestId('code-run')).toBeNull()
    rerender(<MessageBubble message={runMessage} showCodeMode />)
    expect(screen.getByTestId('code-run')).toBeInTheDocument()
  })
})
