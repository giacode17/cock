"""Client wrapper around the CockroachDB Cloud Managed MCP Server.

The agent's runtime structured + vector queries all go through this module
(per the hackathon requirement to use the Managed MCP Server), rather than a
direct SQL connection. Seeding (db/seed_data.py) and schema setup
(db/schema.sql) intentionally bypass this and connect directly — that's an
ops task, not part of the agent.

NOTE ON THE NAME: this package is called `crdb_mcp`, not `mcp` — the `mcp`
name is taken by the official Python MCP SDK we depend on below, and a local
package of the same name would shadow it on sys.path.

AUTH MODEL (confirmed): the Managed MCP Server is a single shared endpoint
(https://cockroachlabs.cloud/mcp) for every cluster. Two headers identify and
authorize each request:
  - `mcp-cluster-id`: which cluster to route to (not secret).
  - `Authorization: Bearer <service account API key>`: CockroachDB Cloud
    supports OAuth 2.1 for interactive clients (what `claude mcp add` sets up
    for Claude Code itself) and, separately, service account API keys for
    headless/autonomous clients like this one — create one in Cloud Console
    under Access Management > Service Accounts, scoped to this cluster.

CONFIRMED LIVE TOOL SCHEMA (via MCPClient.list_tools() against a real cluster
— this is ground truth, not a guess): both `select_query` and `insert_rows`
take `{database, query}` — `query` is a raw SQL string in both cases (for
insert_rows, a full `INSERT INTO table (...) VALUES (...)` statement, not a
table+rows structure). Neither tool supports parameter binding ($1-style
placeholders) — values must be safely inlined into the query text, which is
what `sql_literal()` below is for.

SDK VERSION NOTE: the installed `mcp` package (2.0.0) uses a newer streamable-
HTTP client API than what's documented in most examples circulating online
(which target the 1.x line): the client function is `streamable_http_client`
(not `streamablehttp_client`), it yields a 2-tuple (not 3), and headers are
set via an `httpx2.AsyncClient` (a separate package, not `httpx`) passed as
`http_client=`, rather than a `headers=` kwarg directly.
"""
import json
import os
from typing import Optional

from contextlib import AsyncExitStack

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class MCPClient:
    def __init__(
        self,
        server_url: Optional[str] = None,
        cluster_id: Optional[str] = None,
        auth_token: Optional[str] = None,
        database: Optional[str] = None,
    ):
        self.server_url = server_url or os.environ["CRDB_MCP_SERVER_URL"]
        self.cluster_id = cluster_id or os.environ["CRDB_MCP_CLUSTER_ID"]
        self.auth_token = auth_token or os.environ["CRDB_MCP_AUTH_TOKEN"]
        self.database = database or os.getenv("CRDB_DATABASE", "defaultdb")
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None

    async def __aenter__(self) -> "MCPClient":
        # AsyncExitStack, not manually chained __aenter__/__aexit__ calls: the
        # streamable_http_client/ClientSession context managers use anyio task
        # groups internally, which require strict same-task nesting that hand-
        # rolled __aenter__/__aexit__ storage on instance attributes breaks
        # (surfaces as "cancel scope in a different task" on exit).
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "mcp-cluster-id": self.cluster_id,
        }
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()
        http_client = await self._exit_stack.enter_async_context(httpx2.AsyncClient(headers=headers))
        read, write = await self._exit_stack.enter_async_context(
            streamable_http_client(self.server_url, http_client=http_client)
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self._exit_stack.__aexit__(*exc)

    async def list_tools(self) -> list[str]:
        """Introspect the live server's tool names — run this first against a
        real cluster to confirm select_query/insert_rows actually exist with
        these names before trusting the wrappers below."""
        result = await self._session.list_tools()
        return [t.name for t in result.tools]

    async def call_tool(self, name: str, arguments: dict):
        # Printed (not just returned) so every MCP call is visible in
        # CloudWatch Logs when running on Lambda — the clearest way to show
        # that runtime reads/writes really go through the Managed MCP Server
        # rather than a direct SQL connection. Query text is truncated since
        # vector-search queries inline a ~1024-number embedding literal.
        query_preview = str(arguments.get("query", ""))[:160].replace("\n", " ").strip()
        print(f"[MCP] {name} database={arguments.get('database')} query={query_preview!r}")
        return await self._session.call_tool(name, arguments)

    async def select_query(self, sql: str) -> list[dict]:
        """Run a read-only SELECT via the MCP server's `select_query` tool.

        Covers both plain structured lookups and vector similarity search —
        an `ORDER BY embedding <=> ...` query is still just a SELECT. No
        parameter binding is supported server-side — build `sql` with
        `sql_literal()` for any interpolated values.
        """
        result = await self.call_tool("select_query", {"database": self.database, "query": sql})
        return _extract_rows(result)

    async def insert_rows(self, table: str, rows: list[dict]) -> None:
        """Insert rows via the MCP server's `insert_rows` tool. It takes a raw
        SQL string, not a table+rows structure — this builds one INSERT with a
        VALUES list from `rows` (all rows must share the same columns).
        Requires the service account's Cloud RBAC role to include write access
        — if this errors with a permission/read-only message, check the role
        granted to the service account in Cloud Console."""
        if not rows:
            return
        columns = list(rows[0].keys())
        columns_sql = ", ".join(columns)
        values_sql = ", ".join(
            "(" + ", ".join(sql_literal(row.get(col)) for col in columns) + ")" for row in rows
        )
        query = f"INSERT INTO {table} ({columns_sql}) VALUES {values_sql}"
        await self.call_tool("insert_rows", {"database": self.database, "query": query})


def sql_literal(value) -> str:
    """Render a Python value as a SQL literal for inlining into query text.

    Needed because neither select_query nor insert_rows support parameter
    binding — every value has to be safely embedded in the query string
    itself. Single quotes are doubled per standard SQL string-escaping.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _extract_rows(mcp_result) -> list[dict]:
    """Best-effort extraction of tabular rows from an MCP tool result.

    MCP tool results carry a list of content blocks (usually one JSON/text
    block). Adjust this if the live server returns a different shape.
    """
    for block in getattr(mcp_result, "content", []):
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
    return []
