"""Sanity tests against a seeded CockroachDB instance.

Skipped automatically if the required env vars aren't set (e.g. in CI without
real credentials) — these are integration tests, not unit tests, since the
whole point of this module is exercising the real MCP + Bedrock round trip.

Run with: pytest tests/test_tools.py
"""
import os

import pytest

from agent import tools
from agent.constants import MOCK_USER_ID

requires_live_backend = pytest.mark.skipif(
    not os.getenv("CRDB_MCP_SERVER_URL") or not os.getenv("ANTHROPIC_API_KEY"),
    reason="requires CRDB_MCP_SERVER_URL + a seeded CockroachDB cluster + AWS credentials",
)


@requires_live_backend
@pytest.mark.asyncio
async def test_get_user_plan_returns_seeded_plan():
    plan = await tools.get_user_plan(MOCK_USER_ID)
    assert plan, "expected the seeded mock user to have a current plan"
    assert plan.get("plan_name") == "Summit Bronze HDHP"


@requires_live_backend
@pytest.mark.asyncio
async def test_search_plan_docs_finds_relevant_chunk():
    plan = await tools.get_user_plan(MOCK_USER_ID)
    chunks = await tools.search_plan_docs(plan["plan_id"], "what do I pay for an ER visit?", top_k=3)
    assert chunks
    assert any("emergency" in c["chunk_text"].lower() or "er" in c["chunk_text"].lower() for c in chunks)


@requires_live_backend
@pytest.mark.asyncio
async def test_get_visit_history_returns_seeded_visits():
    visits = await tools.get_visit_history(MOCK_USER_ID)
    assert len(visits) >= 5
