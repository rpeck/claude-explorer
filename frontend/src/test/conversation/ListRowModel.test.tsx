/**
 * B52 (issue #52), "one name everywhere": the model chip of a Conversation
 * List row (`data-testid="list-row-model"`) shows `modelDisplayName(model)`,
 * with the raw id as its title. A row without a model has no chip.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { render, screen, within } from '../utils'
import { ConversationList } from '../../components/conversation/ConversationList'
import { server } from '../mocks/server'
import { mockConversations } from '../mocks/data'
import { serveApp } from '../sources/fakeRegistry'

// The list's providers ask for the preferences and the bookmarks.
beforeEach(() => serveApp({}))

const base = mockConversations[0]
const rows = [
  { ...base, uuid: 'b52-row-opus', name: 'Row with an Opus model', model: 'claude-opus-5-5', is_starred: false },
  { ...base, uuid: 'b52-row-codex', name: 'Row with a Codex model', model: 'gpt-5.6-codex', is_starred: false },
  { ...base, uuid: 'b52-row-none', name: 'Row without a model', model: '', is_starred: false },
]

async function row(title: string): Promise<HTMLElement> {
  server.use(http.get('/api/conversations', () => HttpResponse.json(rows)))
  render(<ConversationList />)
  const name = await screen.findByText(title)
  const el = name.closest<HTMLElement>('[role="button"]')
  expect(el, `the list row of "${title}"`).not.toBeNull()
  return el!
}

describe('the model chip of a list row', () => {
  it('claude-opus-5-5 shows "Opus 5.5", with the raw id as its title', async () => {
    const chip = within(await row('Row with an Opus model')).getByTestId('list-row-model')
    expect(chip).toHaveTextContent(/^Opus 5\.5$/)
    expect(chip).toHaveAttribute('title', 'claude-opus-5-5')
  })

  it('gpt-5.6-codex shows the id as it is', async () => {
    const chip = within(await row('Row with a Codex model')).getByTestId('list-row-model')
    expect(chip).toHaveTextContent(/^gpt-5\.6-codex$/)
    expect(chip).toHaveAttribute('title', 'gpt-5.6-codex')
  })

  it('a row without a model has no chip', async () => {
    const el = await row('Row without a model')
    // The other rows prove that the chip exists; this row has none.
    expect(screen.getAllByTestId('list-row-model')).toHaveLength(2)
    expect(within(el).queryByTestId('list-row-model')).toBeNull()
  })
})
