# Hackathon Submission — Insurance Navigator Agent

- **Repo**: https://github.com/giacode17/cock
- **Demo**: https://giacode17.github.io
- **API**: https://gzjlbbqcb7otg5xtebur7766o40jatel.lambda-url.us-west-2.on.aws

## Architecture

![Architecture diagram: a browser request flows through a Lambda Function URL into a container-image Lambda running the agent, which calls Anthropic for reasoning, Bedrock to embed the query, and the CockroachDB Managed MCP Server for every structured and vector read/write; a dashed path shows the one exception, the ops-time seed script writing to CockroachDB directly.](docs/architecture.svg)

*Every runtime read/write goes through the CockroachDB Managed MCP Server — the only exception is
`db/seed_data.py`, an ops-time script that writes directly, shown as the dashed path.*

## Description

Insurance Navigator Agent helps an employee actually use their employer-provided health insurance, instead
of guessing. It stores the user's plan details, and then:

1. **Symptom → care recommendation.** Describe a symptom or situation, and the agent pulls your specific
   plan and searches the real Summary of Benefits and Coverage (SBC) document text for relevant coverage
   language, then recommends where to go — PCP, urgent care, telehealth, or ER — with a cost-tier estimate
   grounded in your plan's actual deductible/copay/coinsurance numbers, not generic advice.
2. **Plan Q&A.** Ask direct questions like "does my plan cover physical therapy?" or "what's my
   deductible?" and get answers sourced from the real plan document.
3. **Renewal recommendation.** At renewal time, the agent pulls your full visit history — what care you
   used, what it actually cost, how satisfied you were — and reasons over it against every available plan
   to recommend switching or staying, with a concrete dollar comparison built from your real usage pattern.

The backend is a Claude-powered agent (Anthropic API, tool-use loop) running as a containerized AWS Lambda
function behind a Lambda Function URL, with a lightweight HTML/JS chat UI as the frontend.

## CockroachDB tools used

- **Managed MCP Server** — every structured read and write the agent makes at runtime goes through the
  Managed MCP Server (`https://cockroachlabs.cloud/mcp`), not a direct SQL connection. `get_user_plan`,
  `get_visit_history`, and `list_available_plans` call the server's `select_query` tool; `log_visit` and
  `log_message` call `insert_rows`. (Seeding/schema setup is the one deliberate exception — an ops-time
  task that connects directly, since it's not part of the agent's own behavior.)
- **Distributed Vector Indexing** — each plan's SBC text is chunked, embedded (Bedrock Titan Text
  Embeddings V2, 1024 dims), and stored in a `VECTOR(1024)` column with a distributed vector index
  (`plan_document_chunks`). Every recommendation and plan-coverage answer runs a cosine-distance similarity
  search (`ORDER BY embedding <=> ...`) against that index through the Managed MCP Server — this is what
  grounds answers in the plan's actual document language instead of hardcoded rules.

## AWS tools used

- **Amazon Bedrock** — Titan Text Embeddings V2 generates the embeddings for both the SBC document chunks
  (at seed time) and the user's query text (at search time): the retrieval half of the RAG pipeline behind
  plan-grounded answers.
- **AWS Lambda** — the entire agent backend (FastAPI + the Claude tool-use loop + the MCP client) runs as
  a container-image Lambda function (arm64), invoked through a Lambda Function URL. A Function URL was
  used instead of API Gateway specifically because API Gateway's HTTP API integrations hard-cap at ~30
  seconds regardless of the Lambda's own configured timeout, which the multi-turn renewal-reasoning flow
  can exceed.

## Feedback on CockroachDB AI tools (optional, but genuine)

- The Managed MCP Server's tool surface (`select_query`, `insert_rows`, ...) is documented for IDE-style
  clients (Claude Code/Cursor/VS Code) but not for headless programmatic clients — we had to connect and
  call `list_tools()` against a live cluster to discover the actual argument shapes (a `database` field is
  required on every call; `insert_rows` takes a raw SQL string, not a table+rows structure; neither tool
  supports parameter binding at all). Docs aimed at a Python/headless integration path would have saved
  real debugging time.
- Relatedly, no parameter binding on `select_query`/`insert_rows` means every value has to be manually
  inlined into SQL text — for a 1024-dim vector literal this is enough text to hit the server's 16384-char
  query length cap unless you trim float precision first. A parameterized variant (or a documented length
  budget) would remove a sharp edge.
- Distributed Vector Indexing itself worked well once the schema was right — pgvector-compatible syntax
  made it a fast port from prior pgvector experience.
