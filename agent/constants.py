"""Shared constants used across the Insurance Navigator Agent."""
import os

# Fixed mock user for the hackathon demo — seeded by db/seed_data.py.
MOCK_USER_ID = os.getenv("MOCK_USER_ID", "11111111-1111-1111-1111-111111111111")

EMBEDDING_DIMENSIONS = 1024
BEDROCK_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
