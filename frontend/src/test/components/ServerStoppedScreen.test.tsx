/**
 * UX.md → "Quit (power button)": the stopped screen replaces the whole app.
 * A screen-reader user must hear that it happened, so the screen moves
 * focus to its heading and puts the instruction in a status region.
 */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ServerStoppedScreen } from '../../components/ServerStoppedScreen'

describe('ServerStoppedScreen accessibility', () => {
  it('moves focus to the heading when it appears', () => {
    render(<ServerStoppedScreen />)
    const heading = screen.getByRole('heading', { name: 'Claude Explorer has stopped' })
    expect(heading).toHaveFocus()
  })

  it('announces the instruction in a polite status region', () => {
    render(<ServerStoppedScreen />)
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-live', 'polite')
    expect(status).toHaveTextContent('You can close this tab.')
  })
})
