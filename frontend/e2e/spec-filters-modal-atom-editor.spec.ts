// Spec-driven test: Manage Filters modal — atom editor.
//
// UX.md clauses verified (lines 615-738, "Composable filters" §
// "Manage Filters modal"):
//   - "Atom editor: name, Behavior (Hide matches / Show only matches),
//     mode (glob/regex), patterns (one per line), enabled toggle. The
//     Name input auto-fills from the first usable pattern (≥3
//     alphanumeric chars after stripping glob/regex meta-characters)
//     until the user manually edits the name; clearing the name resumes
//     auto-fill."
//   - "Used by: line sits directly under the name input and lists the
//     groups that reference the current filter."
//   - Plain-English summary line below the controls: Hide + ≥1 pattern
//     contains "Hides" + "match any of"; show-only contains "Shows only";
//     empty patterns yields a "no patterns" message.
//   - Behavior radio updates summary in real time.
//   - Mode radio re-evaluates the active filter against current
//     conversations within the same session.
//   - Enabled toggle synced both directions (row → editor, editor → row).
//   - Trash icon present on every row of the left list.
//
// NO APP CODE was read while writing this test.

import { test, expect, withNetRetry } from './fixtures'
import { makeSummary } from './fixtures'

const conversations = [
  makeSummary({ uuid: 'c-foo', name: 'Foo morning' }),
  makeSummary({ uuid: 'c-bar', name: 'Bar afternoon' }),
  makeSummary({ uuid: 'c-foobar', name: 'Foo and Bar' }),
]

async function openModal(page: import('@playwright/test').Page) {
  // Pin to the contract-implicit testid; the migration banner exposes
  // aria-label="Filter update" which would conflict with a /filter/i
  // label fallback in strict-mode locators.
  const picker = page.getByTestId('active-filter-select')
  await picker.click()
  const manageOpt = page.getByRole('option', { name: /manage filters/i }).or(
    page.getByRole('menuitem', { name: /manage filters/i }),
  ).first()
  await expect(manageOpt).toBeVisible()
  await manageOpt.click()
  const modal = page.getByRole('dialog')
  await expect(modal).toBeVisible()
  return modal
}

test.describe('Manage Filters modal — atom editor', () => {
  test('Behavior radio (Hide / Show only) is present', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'My atom',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)

    // Select the row to edit
    await modal.getByText(/My atom/).first().click()

    await expect(modal.getByRole('radio', { name: /hide matches/i })).toBeVisible()
    await expect(modal.getByRole('radio', { name: /show only matches/i })).toBeVisible()
  })

  test('Mode radio (Glob / Regex) is present', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'My atom',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/My atom/).first().click()

    await expect(modal.getByRole('radio', { name: /^glob$/i })).toBeVisible()
    await expect(modal.getByRole('radio', { name: /^regex$/i })).toBeVisible()
  })

  test('Patterns textarea is present and editable (one per line)', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'My atom',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/My atom/).first().click()

    const textarea = modal.getByRole('textbox', { name: /patterns/i })
    await expect(textarea).toBeVisible()
    await expect(textarea).toHaveValue(/Foo/)
  })

  test('Summary line copy: hide + patterns', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'Hide Foo',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/Hide Foo/).first().click()

    // Loose key-phrase match: "Hides" + "match any of"
    await expect(modal.getByText(/Hides/i)).toBeVisible()
    await expect(modal.getByText(/match any of/i)).toBeVisible()
  })

  test('Summary line copy: show-only + patterns', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'Show Foo',
              enabled: true,
              behavior: 'show-only',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/Show Foo/).first().click()

    await expect(modal.getByText(/Shows only/i)).toBeVisible()
    await expect(modal.getByText(/match any of/i)).toBeVisible()
  })

  test('Summary line copy: empty patterns', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'Empty atom',
              enabled: true,
              behavior: 'hide',
              patterns: [],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/Empty atom/).first().click()

    await expect(modal.getByText(/no patterns/i)).toBeVisible()
  })

  test('Behavior radio updates summary in real time (no save needed)', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'Toggleme',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/Toggleme/).first().click()

    // Initially hide => "Hides" in summary.
    await expect(modal.getByText(/Hides/i)).toBeVisible()

    // Click Show only.
    await modal.getByRole('radio', { name: /show only matches/i }).click()

    // Summary updates without saving.
    await expect(modal.getByText(/Shows only/i)).toBeVisible()
  })

  test('Mode radio re-evaluates active filter immediately within session', async ({ page, mockBackend }) => {
    // Glob "^Bar" matches nothing (no titles start with literal `^`).
    // Switching to regex mode + Save, "^Bar" anchors at start and
    // matches "Bar afternoon" (and not "Foo and Bar"). The spec
    // ("re-evaluates within the same session") rules out a reload-only
    // contract: after Save the sidebar must reflect the new mode without
    // page.reload(). Spec is silent on whether the unsaved-draft
    // preview also re-evaluates; this test asserts the post-Save
    // in-session re-eval, which is the load-bearing contract.
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'ModeTest',
              enabled: true,
              behavior: 'show-only',
              patterns: ['^Bar'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: 'a-1',
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))

    // Glob "^Bar" matches no titles → show-only with empty match → 0 rows.
    await expect(page.getByText('Foo morning')).toHaveCount(0)
    await expect(page.getByText('Bar afternoon')).toHaveCount(0)

    const modal = await openModal(page)
    await modal.getByText(/ModeTest/).first().click()

    // Switch to regex mode → "^Bar" now anchors at start.
    await modal.getByRole('radio', { name: /^regex$/i }).click()

    // Save the change (this is what persists the new mode into the
    // active filter; without saving, the live-preview contract is
    // ambiguous in UX.md).
    await modal.getByTestId('filter-editor-save').click()

    // Close the modal.
    await page.keyboard.press('Escape')

    // Same session, no reload: "Bar afternoon" appears (regex anchors).
    await expect(page.getByText('Bar afternoon')).toBeVisible()
  })

  test('Name auto-fill from first usable pattern (start)', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {},
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)

    // Click "New filter" or equivalent affordance.
    const newBtn = modal.getByRole('button', { name: /new filter|add filter|\+ new/i }).first()
    await expect(newBtn).toBeVisible()
    await newBtn.click()

    const nameInput = modal.getByRole('textbox', { name: /^name/i })
    const patterns = modal.getByRole('textbox', { name: /patterns/i })
    await patterns.fill('hello-world')
    // Debounced ~300ms; wait for it.
    await expect(nameInput).toHaveValue(/hello.?world|hello/i, { timeout: 2000 })
  })

  test('Name auto-fill stops once user manually edits name', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {},
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await page.clock.install()
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    const newBtn = modal.getByRole('button', { name: /new filter|add filter|\+ new/i }).first()
    await newBtn.click()

    const nameInput = modal.getByRole('textbox', { name: /^name/i })
    const patterns = modal.getByRole('textbox', { name: /patterns/i })

    await nameInput.fill('MyManualName')
    await patterns.fill('something-else')
    // Name should NOT be overwritten: run the 300 ms prefill debounce for
    // certain on the page clock (2026-10-24: a real 500 ms sleep).
    await page.clock.runFor(500)
    await expect(nameInput).toHaveValue('MyManualName')
  })

  test('Clearing name resumes auto-fill', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {},
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await page.clock.install()
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    const newBtn = modal.getByRole('button', { name: /new filter|add filter|\+ new/i }).first()
    await newBtn.click()

    const nameInput = modal.getByRole('textbox', { name: /^name/i })
    const patterns = modal.getByRole('textbox', { name: /patterns/i })

    await nameInput.fill('ManualName')
    await patterns.fill('initial-pat')
    await page.clock.runFor(500) // the prefill debounce runs for certain
    await expect(nameInput).toHaveValue('ManualName')

    // Clear name.
    await nameInput.fill('')

    // Type a new pattern; auto-fill resumes.
    await patterns.fill('resume-pat')
    await expect(nameInput).toHaveValue(/resume/i, { timeout: 2000 })
  })

  test('Enabled toggle: row → editor sync', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'TogglerOne',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/TogglerOne/).first().click()

    // The modal's two-pane layout puts the row checkboxes (left pane)
    // BEFORE the editor checkbox (right pane) in DOM order. So
    // `first()` is the row toggle and `last()` is the editor toggle.
    // (Spec-ambiguity flag #2: tests originally used first()/nth(1)
    // assuming the opposite order.)
    const allEnabled = modal.getByRole('checkbox', { name: /enabled/i })
    const rowEnabled = allEnabled.first()
    const editorEnabled = allEnabled.last()

    // Editor's enabled checkbox starts checked.
    await expect(editorEnabled).toBeChecked()

    // Click row toggle.
    await rowEnabled.click()

    // Editor reflects new state.
    await expect(editorEnabled).not.toBeChecked()
  })

  test('Enabled toggle: editor → row sync', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'TogglerTwo',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/TogglerTwo/).first().click()

    // Row checkbox is first in DOM (left pane); editor checkbox is last
    // (right pane). See row → editor test for context on the order.
    const allEnabled = modal.getByRole('checkbox', { name: /enabled/i })
    const rowEnabled = allEnabled.first()
    const editorEnabled = allEnabled.last()

    // Confirm both are checked initially.
    await expect(editorEnabled).toBeChecked()
    // Toggle in editor.
    await editorEnabled.click()
    await expect(editorEnabled).not.toBeChecked()
    // Row reflects new state.
    await expect(rowEnabled).not.toBeChecked()
  })

  test('Used-by line under name input lists referencing groups', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-foo': {
              id: 'a-foo',
              type: 'atom',
              name: 'AtomFoo',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
            'g-1': {
              id: 'g-1',
              type: 'group',
              name: 'GroupOne',
              enabled: true,
              match: 'all',
              childIds: ['a-foo'],
            },
            'g-2': {
              id: 'g-2',
              type: 'group',
              name: 'GroupTwo',
              enabled: true,
              match: 'any',
              childIds: ['a-foo'],
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)
    await modal.getByText(/AtomFoo/).first().click()

    // Used-by line names BOTH groups inline.
    // (Group names also appear in the left list rows, so use .first()
    // to avoid strict-mode violations.)
    await expect(modal.getByText(/used by/i)).toBeVisible()
    await expect(modal.getByText(/GroupOne/i).first()).toBeVisible()
    await expect(modal.getByText(/GroupTwo/i).first()).toBeVisible()
  })

  test('Trash icon present on every row (count check)', async ({ page, mockBackend }) => {
    await mockBackend({
      conversations,
      preferences: {
        filters: {
          nodes: {
            'a-1': {
              id: 'a-1',
              type: 'atom',
              name: 'AtomOne',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Foo*'],
              mode: 'glob',
              target: 'title',
            },
            'a-2': {
              id: 'a-2',
              type: 'atom',
              name: 'AtomTwo',
              enabled: true,
              behavior: 'show-only',
              patterns: ['*Bar*'],
              mode: 'glob',
              target: 'title',
            },
            'a-3': {
              id: 'a-3',
              type: 'atom',
              name: 'AtomThree',
              enabled: true,
              behavior: 'hide',
              patterns: ['*Baz*'],
              mode: 'glob',
              target: 'title',
            },
          },
          activeId: null,
          _migratedV1: true,
          _migratedV2: true,
        },
      },
    })
    await withNetRetry(page, () => page.goto('/'))
    const modal = await openModal(page)

    const deletes = modal.getByRole('button', { name: /^delete/i })
    await expect(deletes).toHaveCount(3)
  })
})
