import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '../utils';
import { ConversationList } from '../../components/conversation/ConversationList';
import { server } from '../mocks/server';
import { http, HttpResponse } from 'msw';
import { mockConversations } from '../mocks/data';

describe('ConversationList', () => {
  it('renders loading skeletons while fetching', () => {
    // Delay the response to see loading state
    server.use(
      http.get('/api/conversations', async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
        return HttpResponse.json(mockConversations);
      })
    );

    render(<ConversationList />);

    // Should show skeleton loading state
    const skeletons = document.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders empty state when no conversations', async () => {
    server.use(
      http.get('/api/conversations', () => {
        return HttpResponse.json([]);
      })
    );

    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('No conversations yet')).toBeInTheDocument();
    });
  });

  it('renders "No conversations found" when search has no results', async () => {
    server.use(
      http.get('/api/conversations', () => {
        return HttpResponse.json([]);
      })
    );

    render(<ConversationList searchQuery="nonexistent" />);

    await waitFor(() => {
      expect(screen.getByText('No conversations found')).toBeInTheDocument();
    });
  });

  it('renders conversation items sorted with starred first', async () => {
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Starred')).toBeInTheDocument();
    });

    // Check that starred conversations appear
    expect(screen.getByText('Building a React App')).toBeInTheDocument();
    expect(screen.getByText('Debugging API Errors')).toBeInTheDocument();

    // Check that unstarred conversations also appear
    expect(screen.getByText('Python Data Analysis')).toBeInTheDocument();
    expect(screen.getByText('Learning TypeScript')).toBeInTheDocument();
  });

  it('shows star icon for starred conversations', async () => {
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Building a React App')).toBeInTheDocument();
    });

    // Starred conversations should have filled star icons
    const starIcons = document.querySelectorAll('.fill-yellow-400');
    expect(starIcons.length).toBe(2); // Two starred conversations
  });

  it('shows branch indicator for branched conversations', async () => {
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Python Data Analysis')).toBeInTheDocument();
    });

    // The branched conversation should have GitBranch icon
    // Python Data Analysis has has_branches: true
    const branchIcons = document.querySelectorAll('svg.lucide-git-branch');
    expect(branchIcons.length).toBe(1);
  });

  it('displays model badge, date, and message count', async () => {
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Building a React App')).toBeInTheDocument();
    });

    // Check for model badges
    expect(screen.getAllByText('claude-sonnet-4-6').length).toBeGreaterThan(0);
    expect(screen.getByText('claude-3-opus-20240229')).toBeInTheDocument();

    // Check for message counts
    expect(screen.getByText('10 msgs')).toBeInTheDocument();
    expect(screen.getByText('8 msgs')).toBeInTheDocument();
  });

  it('shows error state when API fails', async () => {
    server.use(
      http.get('/api/conversations', () => {
        return new HttpResponse(null, { status: 500 });
      })
    );

    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load conversations')).toBeInTheDocument();
    });
  });

  it('filters conversations by search query', async () => {
    server.use(
      http.get('/api/conversations', ({ request }) => {
        const url = new URL(request.url);
        const search = url.searchParams.get('search');

        if (search === 'React') {
          return HttpResponse.json([mockConversations[0]]); // Only React conversation
        }
        return HttpResponse.json(mockConversations);
      })
    );

    render(<ConversationList searchQuery="React" />);

    await waitFor(() => {
      expect(screen.getByText('Building a React App')).toBeInTheDocument();
    });

    // Other conversations should not be visible
    expect(screen.queryByText('Python Data Analysis')).not.toBeInTheDocument();
  });

  // F12 (2026-05-29): pins the per-row source indicator for the third
  // source value (CLAUDE_COWORK). Pre-fix code falls through to the
  // CLAUDE_AI/"Claude Desktop" arm, so the Cowork row gets a blue
  // MessageSquare with title="Claude Desktop" — actively wrong.
  it('renders Cowork rows with the purple Sparkles source indicator (not Desktop)', async () => {
    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Cowork Session With Claude')).toBeInTheDocument();
    });

    // The Cowork row's icon-wrapping <span> exposes title="Claude Cowork".
    // RTL's getByTitle is the user-centric query (the title attribute is
    // part of the accessible-name computation and surfaces as the hover
    // tooltip the user actually sees).
    expect(screen.getByTitle('Claude Cowork')).toBeInTheDocument();

    // Bidirectional assertion: a Cowork row must NOT carry the
    // "Claude Desktop" title — that was the pre-fix wrong behavior.
    // The Desktop title still exists on the three CLAUDE_AI rows, so
    // we count: 3 Desktop titles (conv-1/2/3), not 4.
    const desktopTitles = screen.getAllByTitle('Claude Desktop');
    expect(desktopTitles).toHaveLength(3);

    // And the Cowork glyph is the purple Sparkles icon, not the blue
    // MessageSquare. lucide-react emits a class `lucide-sparkles` on
    // the rendered SVG.
    const cowork = screen.getByTitle('Claude Cowork');
    const sparkles = cowork.querySelector('svg.lucide-sparkles');
    expect(sparkles).not.toBeNull();
    expect(sparkles).toHaveClass('text-purple-500');
  });

  // Recovery 2026-05-30 REG-1: when `groupByProject=true` and a group's
  // members are ALL CLAUDE_AI, the group header renders the canonical
  // SourceBadge (row variant — title="Claude Desktop") for the source
  // indicator. Pre-fix this branch referenced a removed `MessageSquare`
  // import and threw `ReferenceError: MessageSquare is not defined` at
  // first render. The new code uses `<SourceBadge source="CLAUDE_AI"
  // variant="row" />` which inherits the same blue MessageSquare visual
  // via the canonical source-preset map.
  it('groupByProject: all-CLAUDE_AI group header renders SourceBadge without ReferenceError', async () => {
    server.use(
      http.get('/api/conversations', () => {
        // Three CLAUDE_AI rows tagged into the same organization → one
        // group whose every member is CLAUDE_AI → hits the
        // `groupConvs.every((c) => c.source === 'CLAUDE_AI')` branch.
        return HttpResponse.json([
          {
            ...mockConversations[0],
            uuid: 'grp-ai-1',
            organization_name: 'Acme Org',
            organization_id: 'org-acme',
            source: 'CLAUDE_AI',
          },
          {
            ...mockConversations[1],
            uuid: 'grp-ai-2',
            organization_name: 'Acme Org',
            organization_id: 'org-acme',
            source: 'CLAUDE_AI',
          },
          {
            ...mockConversations[2],
            uuid: 'grp-ai-3',
            organization_name: 'Acme Org',
            organization_id: 'org-acme',
            source: 'CLAUDE_AI',
          },
        ]);
      })
    );

    // Pre-fix this render throws synchronously; just asserting the
    // group label appears proves render completed without ReferenceError.
    render(<ConversationList groupByProject={true} />);

    await waitFor(() => {
      expect(screen.getByText('Acme Org')).toBeInTheDocument();
    });

    // Bidirectional: the group header MUST carry a Desktop title from
    // the canonical SourceBadge — same source→icon map the per-row
    // indicator uses, so adding/renaming/recoloring is a single-file
    // change going forward.
    const desktopTitles = screen.getAllByTitle('Claude Desktop');
    // 3 per-row indicators + 1 group header indicator = 4 sites.
    expect(desktopTitles.length).toBeGreaterThanOrEqual(4);
  });

  // Issue #44: a leading emoji looked like an app icon. The row keeps the
  // exact API title, the emoji gets the title-emoji class (grayscale), and
  // a hover shows the exact title.
  it('keeps the exact title with an emoji, styles the emoji, and shows the title on hover', async () => {
    server.use(
      http.get('/api/conversations', () =>
        HttpResponse.json([{ ...mockConversations[0], name: '⚡ Weekly report' }]),
      ),
    );

    render(<ConversationList />);

    const emoji = await waitFor(() => {
      const el = document.querySelector('span.title-emoji');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(emoji.textContent).toBe('⚡');
    const title = emoji.parentElement as HTMLElement;
    expect(title.textContent).toBe('⚡ Weekly report');
    expect(title).toHaveAttribute('title', '⚡ Weekly report');
  });

  it('styles an emoji in a subagent name', async () => {
    server.use(
      http.get('/api/conversations', () =>
        HttpResponse.json([
          {
            ...mockConversations[0],
            name: 'Parent session',
            subagents: [
              {
                uuid: 'sub-1',
                agent_id: 'a1',
                name: '🔍 Explore the parser',
                model: 'claude-sonnet-4-6',
                created_at: '2026-04-01T10:00:00Z',
                updated_at: '2026-04-01T10:00:00Z',
                message_count: 3,
              },
            ],
          },
        ]),
      ),
    );

    render(<ConversationList />);

    await waitFor(() => {
      expect(screen.getByText('Parent session')).toBeInTheDocument();
    });
    // The subagent toggle shows the count of subagents.
    screen.getByRole('button', { name: '1' }).click();
    const emoji = await waitFor(() => {
      const el = document.querySelector('span.title-emoji');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(emoji.textContent).toBe('🔍');
    expect(emoji.parentElement?.textContent).toBe('🔍 Explore the parser');
  });

  it('truncates long titles with ellipsis', async () => {
    server.use(
      http.get('/api/conversations', () => {
        return HttpResponse.json([
          {
            ...mockConversations[0],
            name: 'This is a very long conversation title that should be truncated with ellipsis when displayed in the list',
          },
        ]);
      })
    );

    render(<ConversationList />);

    await waitFor(() => {
      const title = screen.getByText(/This is a very long/);
      expect(title).toHaveClass('truncate');
    });
  });
});
