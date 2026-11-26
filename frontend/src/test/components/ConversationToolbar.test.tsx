import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConversationToolbar } from '../../components/conversation/ConversationToolbar';

/**
 * P1.4 Commit C — ConversationToolbar extracted from ConversationPage.
 *
 * Pins the click-wiring contract for each toolbar button. We render the
 * toolbar in isolation with sensible defaults and assert that:
 *   - Each visible control is in the DOM.
 *   - Clicking each control invokes the right callback with the right
 *     arguments.
 *   - The Show Compactions checkbox only renders when hasCompactMarkers.
 *   - The Re-download button only renders for CLAUDE_AI.
 *   - The Expand All button only renders when showToolCalls.
 *
 * Visual structure (flex-wrap container, etc.) is left to the e2e
 * suites — these unit tests are a contract pin for the parent's button
 * wiring.
 */

// Mock-builder pattern: each `vi.fn<Signature>()` is typed to its real
// callback signature so the props object is assignable to
// `ConversationToolbarProps` without `as unknown as` casts and so
// `.toHaveBeenCalledWith(...)` arg shapes are checked at compile time.
// Recovery 2026-05-30 REG-5: previously `ReturnType<typeof vi.fn>`
// returned `Mock<Procedure | Constructable>` and the spread `{ ...defaults }`
// failed the strict-tsc check at every call site (10 errors).
type MockProps = {
  showToolCalls?: boolean
  setShowToolCalls?: ReturnType<typeof vi.fn<(next: boolean) => void>>
  markPendingRecenter?: ReturnType<typeof vi.fn<() => void>>
  expandAllTools?: boolean
  handleToggleExpandAll?: ReturnType<typeof vi.fn<() => void>>
  isExpandPending?: boolean
  conversationSource?: 'CLAUDE_AI' | 'CLAUDE_CODE' | 'CLAUDE_COWORK'
  handleForceRefetch?: ReturnType<typeof vi.fn<() => void>>
  isRefetching?: boolean
  hasCompactMarkers?: boolean
  hideCompactMarkers?: boolean
  setHideCompactMarkers?: ReturnType<typeof vi.fn<(next: boolean) => void>>
  copiedAll?: boolean
  handleCopyAll?: ReturnType<typeof vi.fn<() => void>>
  setMarkdownDialogOpen?: ReturnType<typeof vi.fn<(open: boolean) => void>>
  handleExportPdf?: ReturnType<typeof vi.fn<() => void>>
  isExportingPdf?: boolean
}

function renderToolbar(overrides: MockProps = {}) {
  const defaults = {
    showToolCalls: false,
    setShowToolCalls: vi.fn<(next: boolean) => void>(),
    markPendingRecenter: vi.fn<() => void>(),
    expandAllTools: false,
    handleToggleExpandAll: vi.fn<() => void>(),
    isExpandPending: false,
    conversationSource: 'CLAUDE_AI' as const,
    handleForceRefetch: vi.fn<() => void>(),
    isRefetching: false,
    hasCompactMarkers: false,
    hideCompactMarkers: false,
    setHideCompactMarkers: vi.fn<(next: boolean) => void>(),
    copiedAll: false,
    handleCopyAll: vi.fn<() => void>(),
    setMarkdownDialogOpen: vi.fn<(open: boolean) => void>(),
    handleExportPdf: vi.fn<() => void>(),
    isExportingPdf: false,
  };
  const props = { ...defaults, ...overrides };
  render(<ConversationToolbar {...props} />);
  return props;
}

describe('ConversationToolbar — always-present controls', () => {
  it('renders the Show Tools checkbox, Copy as Markdown, Markdown, and PDF buttons', () => {
    renderToolbar();
    expect(screen.getByTestId('header-show-tools-checkbox')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Copy as Markdown/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Markdown$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^PDF$/ })).toBeInTheDocument();
  });

  it('clicking Show Tools fires setShowToolCalls(true) AND markPendingRecenter()', async () => {
    const user = userEvent.setup();
    const props = renderToolbar();
    await user.click(screen.getByTestId('header-show-tools-checkbox'));
    expect(props.setShowToolCalls).toHaveBeenCalledWith(true);
    expect(props.markPendingRecenter).toHaveBeenCalled();
  });

  it('clicking Copy as Markdown fires handleCopyAll', async () => {
    const user = userEvent.setup();
    const props = renderToolbar();
    await user.click(screen.getByRole('button', { name: /Copy as Markdown/i }));
    expect(props.handleCopyAll).toHaveBeenCalled();
  });

  it('clicking Markdown opens the markdown dialog', async () => {
    const user = userEvent.setup();
    const props = renderToolbar();
    await user.click(screen.getByRole('button', { name: /^Markdown$/ }));
    expect(props.setMarkdownDialogOpen).toHaveBeenCalledWith(true);
  });

  it('clicking PDF fires handleExportPdf', async () => {
    const user = userEvent.setup();
    const props = renderToolbar();
    await user.click(screen.getByRole('button', { name: /^PDF$/ }));
    expect(props.handleExportPdf).toHaveBeenCalled();
  });
});

describe('ConversationToolbar — conditional controls', () => {
  it('Expand All button shows ONLY when showToolCalls=true', () => {
    const { unmount } = render(
      <ConversationToolbar
        showToolCalls={false}
        setShowToolCalls={vi.fn()}
        markPendingRecenter={vi.fn()}
        expandAllTools={false}
        handleToggleExpandAll={vi.fn()}
        isExpandPending={false}
        conversationSource="CLAUDE_AI"
        handleForceRefetch={vi.fn()}
        isRefetching={false}
        hasCompactMarkers={false}
        hideCompactMarkers={false}
        setHideCompactMarkers={vi.fn()}
        copiedAll={false}
        handleCopyAll={vi.fn()}
        setMarkdownDialogOpen={vi.fn()}
        handleExportPdf={vi.fn()}
        isExportingPdf={false}
      />
    );
    expect(
      screen.queryByRole('button', { name: /^(Expand|Collapse)$/i })
    ).not.toBeInTheDocument();
    unmount();
    renderToolbar({ showToolCalls: true });
    expect(
      screen.getByRole('button', { name: /^(Expand|Collapse)$/i })
    ).toBeInTheDocument();
  });

  it('Re-download button shows ONLY for CLAUDE_AI', () => {
    const { unmount } = render(
      <ConversationToolbar
        showToolCalls={false}
        setShowToolCalls={vi.fn()}
        markPendingRecenter={vi.fn()}
        expandAllTools={false}
        handleToggleExpandAll={vi.fn()}
        isExpandPending={false}
        conversationSource="CLAUDE_CODE"
        handleForceRefetch={vi.fn()}
        isRefetching={false}
        hasCompactMarkers={false}
        hideCompactMarkers={false}
        setHideCompactMarkers={vi.fn()}
        copiedAll={false}
        handleCopyAll={vi.fn()}
        setMarkdownDialogOpen={vi.fn()}
        handleExportPdf={vi.fn()}
        isExportingPdf={false}
      />
    );
    expect(
      screen.queryByRole('button', { name: /Re-download/i })
    ).not.toBeInTheDocument();
    unmount();
    renderToolbar({ conversationSource: 'CLAUDE_AI' });
    expect(
      screen.getByRole('button', { name: /Re-download/i })
    ).toBeInTheDocument();
  });

  it('Show Compactions checkbox shows ONLY when hasCompactMarkers=true', () => {
    const { unmount } = render(
      <ConversationToolbar
        showToolCalls={false}
        setShowToolCalls={vi.fn()}
        markPendingRecenter={vi.fn()}
        expandAllTools={false}
        handleToggleExpandAll={vi.fn()}
        isExpandPending={false}
        conversationSource="CLAUDE_AI"
        handleForceRefetch={vi.fn()}
        isRefetching={false}
        hasCompactMarkers={false}
        hideCompactMarkers={false}
        setHideCompactMarkers={vi.fn()}
        copiedAll={false}
        handleCopyAll={vi.fn()}
        setMarkdownDialogOpen={vi.fn()}
        handleExportPdf={vi.fn()}
        isExportingPdf={false}
      />
    );
    expect(
      screen.queryByTestId('header-show-compactions-checkbox')
    ).not.toBeInTheDocument();
    unmount();
    renderToolbar({ hasCompactMarkers: true, hideCompactMarkers: false });
    expect(
      screen.getByTestId('header-show-compactions-checkbox')
    ).toBeInTheDocument();
  });

  it('Show Compactions checkbox: when hideCompactMarkers=false, checkbox IS checked', () => {
    renderToolbar({ hasCompactMarkers: true, hideCompactMarkers: false });
    const checkbox = screen.getByTestId(
      'header-show-compactions-checkbox'
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
  });

  it('clicking Show Compactions checkbox fires setHideCompactMarkers(!checked) AND markPendingRecenter', async () => {
    const user = userEvent.setup();
    const props = renderToolbar({
      hasCompactMarkers: true,
      hideCompactMarkers: false,
    });
    // unchecking should call setHideCompactMarkers(true)
    await user.click(screen.getByTestId('header-show-compactions-checkbox'));
    expect(props.setHideCompactMarkers).toHaveBeenCalledWith(true);
    expect(props.markPendingRecenter).toHaveBeenCalled();
  });
});

// Issue #90: the Show Code Mode toggle shows only when the conversation has
// a run. It is a checkbox like Show Tools, and independent of it.
describe('ConversationToolbar — Show Code Mode', () => {
  const base = {
    showToolCalls: false, setShowToolCalls: vi.fn(), markPendingRecenter: vi.fn(), expandAllTools: false,
    handleToggleExpandAll: vi.fn(), isExpandPending: false, conversationSource: 'CLAUDE_CODE' as const,
    handleForceRefetch: vi.fn(), isRefetching: false, hasCompactMarkers: false, hideCompactMarkers: false,
    setHideCompactMarkers: vi.fn(), copiedAll: false, handleCopyAll: vi.fn(), setMarkdownDialogOpen: vi.fn(),
    handleExportPdf: vi.fn(), isExportingPdf: false,
  };

  it('is absent when the conversation has no run', () => {
    render(<ConversationToolbar {...base} hasCodeRuns={false} showCodeMode={false} setShowCodeMode={vi.fn()} />);
    expect(screen.queryByTestId('header-show-code-mode-checkbox')).not.toBeInTheDocument();
  });

  it('shows with a run, and a click turns it on without touching Show Tools', async () => {
    const user = userEvent.setup();
    const setShowCodeMode = vi.fn<(next: boolean) => void>();
    const setShowToolCalls = vi.fn<(next: boolean) => void>();
    render(
      <ConversationToolbar
        {...base}
        setShowToolCalls={setShowToolCalls}
        hasCodeRuns
        showCodeMode={false}
        setShowCodeMode={setShowCodeMode}
      />,
    );
    const box = screen.getByTestId('header-show-code-mode-checkbox');
    expect(screen.getByTestId('header-show-code-mode-control')).toHaveTextContent('Show Code Mode');
    await user.click(box);
    expect(setShowCodeMode).toHaveBeenCalledWith(true);
    expect(setShowToolCalls).not.toHaveBeenCalled();
  });
});
