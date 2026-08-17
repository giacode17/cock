"""Thin FastAPI layer over the agent orchestrator.

Run locally:
    uvicorn api.app:app --reload
Then open frontend/index.html (it points at http://localhost:8000 by default).

Wrapped for Lambda later via api/lambda_handler.py — not deployed in this pass.
"""
from __future__ import annotations

from dotenv import load_dotenv

# Must run before importing agent modules: unlike scripts/run_agent_cli.py and
# db/seed_data.py, nothing else loads .env when this app is started fresh by
# uvicorn (it doesn't inherit a shell that's sourced .env), so credentials
# like ANTHROPIC_API_KEY would otherwise be missing at request time.
load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent import orchestrator, tools  # noqa: E402

app = FastAPI(title="Insurance Navigator Agent")

# Wide-open CORS for local hackathon demo purposes only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    user_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str


class LogVisitRequest(BaseModel):
    user_id: str
    plan_id: str
    symptom_description: str
    recommended_care_type: str
    estimated_cost_tier: str
    estimated_cost_low: float | None = None
    estimated_cost_high: float | None = None
    actual_care_type: str | None = None
    actual_cost: float | None = None
    satisfaction_rating: int | None = None


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    reply = await orchestrator.recommend_for_symptom(req.user_id, req.message)
    return ChatResponse(reply=reply)


@app.get("/renewal/{user_id}", response_model=ChatResponse)
async def renewal(user_id: str) -> ChatResponse:
    reply = await orchestrator.recommend_renewal(user_id)
    return ChatResponse(reply=reply)


@app.post("/visits")
async def log_visit(req: LogVisitRequest) -> dict:
    """Direct visit logging (e.g. the user reporting back what they actually
    did/paid), bypassing the LLM — no reasoning needed for a plain write."""
    return await tools.log_visit(**req.model_dump(exclude_none=True))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
