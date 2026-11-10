Bridge smoke test for the Claude Explorer MCP server (the conversation archive). Its tool prefix depends on the name that the server has in this client, for example mcp__remote-devices__Claude_Explorer__Conversation_Archive___ for the Claude Desktop extension, or mcp__remote-devices__claude-sessions__ for a server named claude-sessions. Make exactly the calls below, in this order, with those tools. Their names end in __list_projects, __list_sessions, __get_session_outline, __get_messages, and __export_session.

Rules:
- Do not retry a call, and do not change its arguments, even if it fails. A failure is a result that I need.
- Do not call any other tool. Exception: if the tools are deferred, load their schemas first (for example with ToolSearch). That is not a deviation.
- For each call, report: the call number, the full tool name, the exact arguments that you sent (JSON), and the first 300 characters of the raw result, or the full error text.

Calls:
1. list_projects with {}
2. list_sessions with {"limit": 1}. Take the "uuid" of the first session as U for calls 3 to 8.
3. get_session_outline with {"conversation_id": U}
4. get_messages with {"conversation_id": U, "positions": [0]}
5. export_session with {"conversation_id": U, "start_position": 0, "end_position": 0}
6. get_session_outline with {"session_id": U}
7. get_messages with {"session_id": U, "positions": [0]}
8. export_session with {"session_id": U, "start_position": 0, "end_position": 0}

At the end, give a table with one row per call: call number, tool, OK or ERROR, and the first line of the error text.
