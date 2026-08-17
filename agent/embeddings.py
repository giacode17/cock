"""Embeddings via Amazon Bedrock Titan Text Embeddings V2.

Used both to embed plan document chunks at seed time (db/seed_data.py) and to
embed the user's query text at search time (agent/tools.py:search_plan_docs).
This is the AWS-side half of the RAG pipeline; the CockroachDB side (storage +
similarity search) lives in crdb_mcp/mcp_client.py.
"""
import json
import os

import boto3

from agent.constants import BEDROCK_EMBEDDING_MODEL_ID, EMBEDDING_DIMENSIONS

_client = None


def _get_client():
    global _client
    if _client is None:
        # Explicit static keys (BEDROCK_ACCESSKEY/BEDROCK_SECRET_ACCESSKEY) take
        # priority since they're what's configured in .env for this project;
        # falling back to None lets boto3's normal credential chain (profile,
        # instance role, etc.) take over if they're not set.
        _client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            aws_access_key_id=os.getenv("BEDROCK_ACCESSKEY") or None,
            aws_secret_access_key=os.getenv("BEDROCK_SECRET_ACCESSKEY") or None,
        )
    return _client


def embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """Return a normalized embedding vector for `text` using Bedrock Titan V2."""
    response = _get_client().invoke_model(
        modelId=BEDROCK_EMBEDDING_MODEL_ID,
        body=json.dumps({
            "inputText": text,
            "dimensions": dimensions,
            "normalize": True,
        }),
    )
    payload = json.loads(response["body"].read())
    return payload["embedding"]


def to_vector_literal(embedding: list[float]) -> str:
    """Format a python float list as a CockroachDB VECTOR literal, e.g. '[0.1,0.2]'.

    6 decimal places, not repr()/full precision: at 1024 dims, full-precision
    floats (~17 sig figs each) push the literal past the MCP server's 16384-
    char query length cap when this is inlined into a select_query call (see
    crdb_mcp/mcp_client.py — no parameter binding, everything is inlined
    text). 6 decimals is already far more precision than cosine similarity
    search needs.
    """
    return "[" + ",".join(f"{float(x):.6f}" for x in embedding) + "]"
