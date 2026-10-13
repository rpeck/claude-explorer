"""MCP server for querying Claude conversation sessions."""

# Single source of truth for the version string. Read by:
#   * pyproject.toml via [tool.hatch.version] path = "mcp_server/__init__.py"
#     (so `pip install claude-explorer` and `importlib.metadata.version`
#     return this string).
#   * scripts/build-mcpb.py to stamp the MCPB manifest.json `version` field.
#   * mcp_server/server.py's FastMCP(version=...) directly via this attribute.
#     (NOT importlib.metadata — the MCPB bundle vendors mcp_server/ as bare
#     directories with no installed claude-explorer package, so metadata
#     lookup would raise PackageNotFoundError at construction time. See
#     mcp_server/tests/test_server_version.py for the regression guard.)
#   * cli/main.py's @click.version_option via importlib.metadata. (Safe
#     here — the CLI ships only as a pip-installed wheel.)
#
# Bumping this here updates pip + MCP serverInfo + MCPB manifest in lockstep.
# See PLANS/2026.06.04-mcpb-bundle.md §9 (Version contract).
__version__ = "1.1.0"
