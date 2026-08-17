"""Tool definitions + implementations for the agent's Claude tool-use loop.

Every tool that touches CockroachDB goes through crdb_mcp.mcp_client.MCPClient
(the Managed MCP Server). Values are inlined into SQL text via sql_literal()
rather than bound as query parameters — the live server's select_query/
insert_rows tools don't support parameter binding (see mcp_client.py).
"""
from agent import embeddings
from crdb_mcp.mcp_client import MCPClient, sql_literal

TOOL_SCHEMAS = [
    {
        "name": "get_user_plan",
        "description": "Look up the insurance plan a user is currently enrolled in, with full plan-level facts (deductible, copays, coinsurance, network type).",
        "input_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "search_plan_docs",
        "description": "Semantic search over the plan's Summary of Benefits and Coverage (SBC) document text. Use this to answer 'does my plan cover X' or to ground a recommendation in the plan's actual language, instead of guessing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "query": {"type": "string", "description": "Natural-language question or symptom to search for relevant plan document text."},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["plan_id", "query"],
        },
    },
    {
        "name": "get_visit_history",
        "description": "Get a user's past logged visits (what they did, what it cost, satisfaction) — used for renewal recommendations.",
        "input_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "list_available_plans",
        "description": "List all seeded insurance plans (for comparing the user's current plan against alternatives at renewal time).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "log_visit",
        "description": "Log a visit recommendation (and, once known, what the user actually did and it cost) to the user's history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "plan_id": {"type": "string"},
                "symptom_description": {"type": "string"},
                "recommended_care_type": {"type": "string", "enum": ["pcp", "urgent_care", "telehealth", "er"]},
                "estimated_cost_tier": {"type": "string", "description": "e.g. '$', '$$', '$$$'"},
                "estimated_cost_low": {"type": "number"},
                "estimated_cost_high": {"type": "number"},
            },
            "required": ["user_id", "plan_id", "symptom_description", "recommended_care_type", "estimated_cost_tier"],
        },
    },
    {
        "name": "log_message",
        "description": "Log a single conversation turn (user or agent) for the agent's conversational memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "role": {"type": "string", "enum": ["user", "agent"]},
                "content": {"type": "string"},
            },
            "required": ["user_id", "role", "content"],
        },
    },
]


async def get_user_plan(user_id: str) -> dict:
    async with MCPClient() as mcp:
        rows = await mcp.select_query(
            f"""
            SELECT p.* FROM insurance_plans p
            JOIN user_plan_enrollments e ON e.plan_id = p.plan_id
            WHERE e.user_id = {sql_literal(user_id)} AND e.is_current = true
            LIMIT 1
            """
        )
    return rows[0] if rows else {}


async def search_plan_docs(plan_id: str, query: str, top_k: int = 5) -> list[dict]:
    query_embedding = embeddings.embed_text(query)
    # Both plan_id and the embedding are inlined via sql_literal/to_vector_literal
    # rather than bound params — the MCP select_query tool has no parameter
    # binding at all (see crdb_mcp/mcp_client.py), so every value has to be
    # safely embedded in the query text itself.
    vector_literal = embeddings.to_vector_literal(query_embedding)
    async with MCPClient() as mcp:
        rows = await mcp.select_query(
            f"""
            SELECT chunk_text, source_doc_name FROM plan_document_chunks
            WHERE plan_id = {sql_literal(plan_id)}
            ORDER BY embedding <=> '{vector_literal}'::VECTOR
            LIMIT {int(top_k)}
            """
        )
    return rows


async def get_visit_history(user_id: str) -> list[dict]:
    async with MCPClient() as mcp:
        rows = await mcp.select_query(
            f"SELECT * FROM visits WHERE user_id = {sql_literal(user_id)} ORDER BY created_at DESC"
        )
    return rows


async def list_available_plans() -> list[dict]:
    async with MCPClient() as mcp:
        return await mcp.select_query("SELECT * FROM insurance_plans")


async def log_visit(**kwargs) -> dict:
    async with MCPClient() as mcp:
        await mcp.insert_rows("visits", [kwargs])
    return {"status": "logged"}


async def log_message(user_id: str, role: str, content: str) -> dict:
    async with MCPClient() as mcp:
        await mcp.insert_rows("conversation_messages", [{"user_id": user_id, "role": role, "content": content}])
    return {"status": "logged"}


TOOL_IMPLS = {
    "get_user_plan": get_user_plan,
    "search_plan_docs": search_plan_docs,
    "get_visit_history": get_visit_history,
    "list_available_plans": list_available_plans,
    "log_visit": log_visit,
    "log_message": log_message,
}
