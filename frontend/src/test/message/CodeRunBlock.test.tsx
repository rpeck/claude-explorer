import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CodeRunBlock } from '@/components/message/blocks/CodeRunBlock'
import { ToolUseBlock } from '@/components/message/blocks/ToolBlocks'
import { codeRunBlock, CM_WORDS, pipeRunBlock } from '../fixtures/codeMode'

/**
 * Issue #90: a Code Mode run is one dark terminal bubble. The title bar
 * names Code Mode, the language, the status, and the line count. The script
 * is highlighted and cut after 15 lines. Then come the numbered calls and
 * what went back to the model. Expand opens an overlay with the tabs
 * Script, Calls, Data flow, and Returned; Esc closes it.
 */
describe('CodeRunBlock', () => {
  it('shows the title bar', () => {
    render(<CodeRunBlock block={codeRunBlock()} />)
    const bar = screen.getByTestId('code-run-title')
    expect(bar).toHaveTextContent('Code Mode')
    expect(bar).toHaveTextContent('javascript')
    expect(bar).toHaveTextContent('completed')
    expect(bar).toHaveTextContent('21 lines')
  })

  it('highlights the script and cuts it after 15 lines', async () => {
    render(<CodeRunBlock block={codeRunBlock()} />)
    const script = screen.getByTestId('code-run-script')
    expect(script.querySelector('.hljs-keyword, .hljs-variable, .hljs-title')).not.toBeNull()
    expect(within(script).getAllByTestId('code-run-line-number')).toHaveLength(15)
    await userEvent.click(screen.getByRole('button', { name: 'Show all 21 lines' }))
    expect(within(screen.getByTestId('code-run-script')).getAllByTestId('code-run-line-number')).toHaveLength(21)
  })

  it('lists the calls in order, with the refused one marked', () => {
    render(<CodeRunBlock block={codeRunBlock()} />)
    const calls = screen.getAllByTestId('code-run-call')
    expect(calls).toHaveLength(2)
    expect(calls[0]).toHaveTextContent('1')
    expect(calls[0]).toHaveTextContent('Bash')
    expect(calls[1]).toHaveTextContent('Write')
    expect(calls[1]).toHaveTextContent('refused')
  })

  it('shows what went back to the model, stdout, and the error', () => {
    render(<CodeRunBlock block={codeRunBlock({ error: 'Error: boom', status: 'failed' })} />)
    expect(screen.getByTestId('code-run-returned')).toHaveTextContent(CM_WORDS.returned)
    expect(screen.getByTestId('code-run-stdout')).toHaveTextContent('listing done')
    expect(screen.getByTestId('code-run-error')).toHaveTextContent('Error: boom')
  })

  it('opens the overlay with four tabs, and Esc closes it', async () => {
    render(<CodeRunBlock block={codeRunBlock()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Expand' }))
    const dialog = await screen.findByRole('dialog')
    const tabs = within(dialog).getAllByRole('tab').map((t) => t.textContent)
    expect(tabs).toEqual(['Script', 'Calls', 'Data flow', 'Returned'])
    // The Script tab shows every line.
    expect(within(dialog).getAllByTestId('code-run-line-number')).toHaveLength(21)
    await userEvent.click(within(dialog).getByRole('tab', { name: 'Data flow' }))
    const lanes = within(dialog).getAllByTestId('sequence-lane').map((l) => l.textContent)
    expect(lanes).toEqual(['Model', 'Script', 'Bash', 'Write'])
    expect(within(dialog).getAllByTestId('sequence-step').length).toBeGreaterThanOrEqual(6)
    await userEvent.click(within(dialog).getByRole('tab', { name: 'Returned' }))
    expect(within(dialog).getByTestId('code-run-returned')).toHaveTextContent(CM_WORDS.returned)
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows a search hit: the whole script, with the match marked', () => {
    render(<CodeRunBlock block={codeRunBlock()} searchQuery="step16" />)
    expect(within(screen.getByTestId('code-run-script')).getAllByTestId('code-run-line-number')).toHaveLength(21)
    expect(screen.getByTestId('code-run-script').querySelector('mark')).not.toBeNull()
  })
})

describe('an unlinked Code Mode call', () => {
  it('says that a script made it', () => {
    render(<ToolUseBlock name="shell" input={{}} fromCodeMode />)
    expect(screen.getByText('made by a Code Mode script')).toBeInTheDocument()
  })
  it('says how many scripts were running', () => {
    render(<ToolUseBlock name="shell" input={{}} fromCodeMode ambiguousWith={['a', 'b']} />)
    expect(screen.getByText('made by a Code Mode script (one of 2 running)')).toBeInTheDocument()
  })
})

/**
 * The common use of Code Mode: one tool's output goes into another tool,
 * and only a small result goes back to the model (issue #90). The backend
 * sets `inputs_from` and `returned_from` from exact text matches.
 */
describe('CodeRunBlock data flow', () => {
  it('marks a call whose input holds an earlier output', () => {
    render(<CodeRunBlock block={pipeRunBlock()} />)
    const calls = screen.getAllByTestId('code-run-call')
    expect(calls[1]).toHaveTextContent('input from call 1')
    expect(calls[0]).not.toHaveTextContent('input from')
  })

  it('draws the pipe from tool to tool and says how much reached the model', async () => {
    render(<CodeRunBlock block={pipeRunBlock()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Expand' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('tab', { name: 'Data flow' }))
    const labels = within(dialog).getAllByTestId('sequence-step-label').map((s) => s.textContent)
    expect(labels).toContain('output of call 1 → call 2')
    expect(within(dialog).getByTestId('code-run-context')).toHaveTextContent(
      'Tool output: 155 characters. Returned to the model: 2 characters.',
    )
  })

  it('says which outputs reached the model', async () => {
    render(<CodeRunBlock block={pipeRunBlock({ returned_from: [0] })} />)
    await userEvent.click(screen.getByRole('button', { name: 'Expand' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('tab', { name: 'Data flow' }))
    expect(within(dialog).getByTestId('code-run-context')).toHaveTextContent('The output of call 1 went back to the model.')
  })
})
