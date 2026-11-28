import { http, HttpResponse } from 'msw';
import {
  mockConversations,
  mockConversationDetail,
  mockConversationTree,
  mockSearchResults,
  mockConfig,
} from './data';

export const handlers = [
  // GET /api/conversations - list conversations
  http.get('/api/conversations', ({ request }) => {
    const url = new URL(request.url);
    const search = url.searchParams.get('search');
    const starred = url.searchParams.get('starred');
    const model = url.searchParams.get('model');

    let result = [...mockConversations];

    // Apply filters. The real backend's `?search=` matcher also
    // includes `summary` matches (PLANS/SPLIT_CONVERSATION_SCHEMA.md
    // preserves that behavior server-side BEFORE the projection), but
    // the mock fixtures here use the SKINNY ConversationListItem
    // shape and don't carry summary — only name + project matching
    // is simulated. The MSW handler doesn't need to be perfectly
    // faithful to the search path since useConversations also filters
    // client-side and the e2e/vitest specs don't cover summary-only
    // matches today.
    if (search) {
      const searchLower = search.toLowerCase();
      result = result.filter(
        (c) =>
          c.name.toLowerCase().includes(searchLower) ||
          (c.project_path ?? '').toLowerCase().includes(searchLower)
      );
    }

    if (starred === 'true') {
      result = result.filter((c) => c.is_starred);
    }

    if (model) {
      result = result.filter((c) => c.model === model);
    }

    return HttpResponse.json(result);
  }),

  // GET /api/conversations/:uuid - get conversation detail
  http.get('/api/conversations/:uuid', ({ params }) => {
    const { uuid } = params;

    if (uuid === 'conv-1') {
      return HttpResponse.json(mockConversationDetail);
    }

    if (uuid === 'not-found') {
      return new HttpResponse(null, { status: 404 });
    }

    // Return a minimal detail for other UUIDs. The detail-endpoint
    // shape still includes summary/human_message_count/git_branch
    // (PLANS/SPLIT_CONVERSATION_SCHEMA.md keeps them on
    // ConversationDetail), so re-add them here with empty defaults
    // since the list-item fixture omits them.
    const conversation = mockConversations.find((c) => c.uuid === uuid);
    if (conversation) {
      return HttpResponse.json({
        ...conversation,
        summary: '',
        human_message_count: 0,
        messages: [],
        current_leaf_message_uuid: '',
      });
    }

    return new HttpResponse(null, { status: 404 });
  }),

  // GET /api/conversations/:uuid/tree - get conversation tree
  http.get('/api/conversations/:uuid/tree', ({ params }) => {
    const { uuid } = params;

    // Return mock tree for conv-2 (which has_branches: true)
    if (uuid === 'conv-2' || uuid === mockConversationTree.uuid) {
      return HttpResponse.json(mockConversationTree);
    }

    // Return empty tree for other conversations
    return HttpResponse.json({
      uuid,
      root_messages: [],
      active_path: [],
    });
  }),

  // GET /api/search - search conversations
  http.get('/api/search', ({ request }) => {
    const url = new URL(request.url);
    const query = url.searchParams.get('q');

    if (!query || query.length < 2) {
      return HttpResponse.json([]);
    }

    // Filter mock results by query
    const results = mockSearchResults.filter(
      (r) =>
        r.conversation_name.toLowerCase().includes(query.toLowerCase()) ||
        r.matching_messages.some((m) =>
          m.snippet.toLowerCase().includes(query.toLowerCase())
        )
    );

    return HttpResponse.json(results);
  }),

  // A2 (issue #92): one machine, mode off, nothing to export.
  http.get('/api/machines', () => {
    return HttpResponse.json({ this_machine_id: null, machines: [] });
  }),
  http.get('/api/machines/export-rules', () => {
    return HttpResponse.json({ version: 1, mode: 'off', allow: [], deny: [], since: null });
  }),
  http.get('/api/machines/export-preview', () => {
    return HttpResponse.json({ files: [], file_count: 0, total_bytes: 0 });
  }),

  // GET /api/config - get app config
  http.get('/api/config', () => {
    return HttpResponse.json(mockConfig);
  }),

  // GET /api/conversations/:uuid/export/markdown - export as markdown
  http.get('/api/conversations/:uuid/export/markdown', ({ request }) => {
    const url = new URL(request.url);
    const includeTools = url.searchParams.get('include_tools') !== 'false';
    const baseMarkdown = '# Building a React App\n\n**Human:** How do I create a React component?\n\n**Claude:** Here\'s how...';
    const markdown = includeTools
      ? baseMarkdown + '\n\n**Tool: read_file**\n```json\n{}\n```'
      : baseMarkdown;
    return new HttpResponse(markdown, {
      headers: {
        'Content-Type': 'text/markdown',
        'Content-Disposition': 'attachment; filename="conversation.md"',
      },
    });
  }),

  // GET /api/conversations/:uuid/export/pdf - export as PDF
  http.get('/api/conversations/:uuid/export/pdf', () => {
    // Return a minimal PDF-like response
    return new HttpResponse(new Blob(['%PDF-1.4 mock'], { type: 'application/pdf' }), {
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': 'attachment; filename="conversation.pdf"',
      },
    });
  }),
];
