/**
 * N8-ui: NoticeList (plan §4.5, "The `notices` show above the messages").
 * The notices are the warnings count, "incomplete: <reason>", "ambiguous
 * current rollout", "Imported from Claude Code", the cloud-sync gate notice,
 * and "N older rollouts are kept in the archive". The page shows them as
 * the backend sends them.
 */
import { createElement, type ComponentType } from 'react'
import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { importModule } from './codexHelpers'

type NoticeModule = { NoticeList: ComponentType<{ notices: string[] }> }

const loadNoticeList = async () =>
  (await importModule<NoticeModule>('@/components/conversation/NoticeList')).NoticeList

const NOTICES = [
  'incomplete: compacted',
  '3 warnings',
  'Imported from Claude Code',
  '2 older rollouts are kept in the archive',
]

describe('NoticeList', () => {
  it('an empty list renders nothing', async () => {
    const NoticeList = await loadNoticeList()
    const { container } = render(createElement(NoticeList, { notices: [] }))
    expect(container).toBeEmptyDOMElement()
  })

  it('each notice is one list item, verbatim and in order', async () => {
    const NoticeList = await loadNoticeList()
    render(createElement(NoticeList, { notices: NOTICES }))
    const list = screen.getByTestId('conversation-notices')
    expect(list.tagName).toBe('UL')
    const items = within(list).getAllByRole('listitem')
    expect(items.map((li) => li.textContent?.trim())).toEqual(NOTICES)
  })
})
