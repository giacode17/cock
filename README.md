# Insurance Navigator Agent

Hackathon submission for the **CockroachDB × AWS Hackathon** ("Build with Agentic Memory").
Built with [Claude Code](https://claude.com/claude-code).

- **Live demo**: https://giacode17.github.io
- **API**: https://gzjlbbqcb7otg5xtebur7766o40jatel.lambda-url.us-west-2.on.aws
- **License**: [MIT](LICENSE)

An agent that stores an employee's insurance plan, recommends where to seek care for a symptom (with a
cost-tier estimate) grounded in the plan's real Summary of Benefits and Coverage (SBC) text, logs visits,
and at renewal time suggests whether to switch plans based on that logged history.

## Architecture

![Architecture diagram: a browser request flows through a Lambda Function URL into a container-image Lambda running the agent, which calls Anthropic for reasoning, Bedrock to embed the query, and the CockroachDB Managed MCP Server for every structured and vector read/write; a dashed path shows the one exception, the ops-time seed script writing to CockroachDB directly.](docs/architecture.svg)

*Every runtime read/write goes through the CockroachDB Managed MCP Server — the only exception is
`db/seed_data.py`, an ops-time script that writes directly, shown as the dashed path.*

## Stack

- **Persistence**: CockroachDB Cloud — structured tables (plans, enrollments, visits, conversation log) +
  a `VECTOR(1024)` column with a distributed vector index over embedded SBC document chunks.
- **Agent runtime DB access**: CockroachDB Cloud **Managed MCP Server** (`crdb_mcp/mcp_client.py`) — all of
  the agent's structured *and* vector queries go through it, not a direct SQL connection.
- **LLM**: Claude via the Anthropic API directly (not Bedrock — faster to wire up under a hackathon
  deadline, no model-access approval wait).
- **Embeddings**: Amazon Bedrock Titan Text Embeddings V2 (`agent/embeddings.py`) — the AWS half of the
  RAG pipeline.
- **Backend**: Python, FastAPI (`api/app.py`), deployed to AWS Lambda (container image, via
  `api/lambda_handler.py` + `infra/`) behind a Lambda Function URL.
- **Frontend**: plain HTML/JS chat page (`frontend/`), pointed at the deployed Lambda by default.

## Deployed

- **Chat UI**: https://giacode17.github.io — a static GitHub Pages copy of `frontend/`, pointed at the
  Lambda endpoint below (see `frontend/app.js:API_BASE`).
- **API** (AWS Lambda, container image, behind a Function URL):
  `https://gzjlbbqcb7otg5xtebur7766o40jatel.lambda-url.us-west-2.on.aws`

```bash
curl https://gzjlbbqcb7otg5xtebur7766o40jatel.lambda-url.us-west-2.on.aws/health
```

Redeploy the API after code changes: `python infra/deploy.py` (idempotent — rebuilds/pushes the image and
updates the existing Lambda function in place). Redeploy the chat UI by pushing `frontend/`'s contents to
the `giacode17.github.io` repo (see git history there for the exact commands used).

## Setup

1. **CockroachDB Cloud**: create a free-tier cluster. Grab the SQL connection string (Cloud Console >
   cluster > Connect) for `COCKROACHDB_CONNECTION_STRING`.
2. **Managed MCP Server auth** — two *separate* things, don't conflate them:
   - Running `claude mcp add cockroachdb-cloud https://cockroachlabs.cloud/mcp --transport http --header
     "mcp-cluster-id: <id>"` registers the server for **Claude Code's own interactive use** (OAuth 2.1,
     browser login) — useful for me to poke at your cluster while we build, but it gives this app's
     backend nothing.
   - This backend is a **headless** client, so it needs a **service account API key** instead: Cloud
     Console > Access Management > Service Accounts > create one, grant it a Cloud RBAC role scoped to
     this cluster (with write access, since `log_visit`/`log_message` insert rows), generate an API key →
     `CRDB_MCP_AUTH_TOKEN`. `CRDB_MCP_CLUSTER_ID` and `CRDB_MCP_SERVER_URL` are already filled in in
     `.env.example`.
3. **Anthropic**: get an API key.
4. **AWS**: configure CLI credentials (`aws configure`), and enable Bedrock model access for
   `amazon.titan-embed-text-v2:0` in your region's Bedrock console (one-time approval step — do this
   early).
5. Copy `.env.example` to `.env` and fill in the remaining values.
6. **Python 3.10+ is required** (the `mcp` SDK doesn't support 3.9). If your default `python3` is older
   (check with `python3 --version`), create an isolated env instead of using it directly — e.g. with conda:
   `conda create -n insurance-navigator python=3.11 -y`, then use that env's `pip`/`python` for everything
   below.
7. `pip install -r requirements.txt`

## Run it

```bash
# 1. Apply schema. If psql errors with a missing root.crt, add &sslrootcert=system to the URL
#    (psql reads the macOS system keychain; this isn't needed for step 2, which uses certifi instead — see Gotchas).
psql "$COCKROACHDB_CONNECTION_STRING" -f db/schema.sql

# 2. Seed mock plans/visits/embeddings
python db/seed_data.py

# 3. Test the agent directly, no API/frontend needed
python scripts/run_agent_cli.py "sharp pain in my lower right side"
python scripts/run_agent_cli.py --renewal

# 4. Or run the API + open the chat UI
uvicorn api.app:app --reload
open frontend/index.html
```

## Repo layout

```
db/            schema.sql, seed_data.py, mock SBC text for 3 plans
crdb_mcp/      MCP client wrapper around the CockroachDB Managed MCP Server
agent/         LLM tool-use loop, tool implementations, embeddings, orchestrator
api/           FastAPI app (local + Lambda handler via Mangum)
infra/         Dockerfile, requirements-lambda.txt, deploy.py (ECR/IAM/Lambda/Function URL via boto3)
frontend/      plain HTML/JS chat UI (also deployed standalone to giacode17.github.io)
scripts/       run_agent_cli.py — local test harness
tests/         integration tests against a live seeded cluster
docs/          architecture.svg
LICENSE        MIT
```

## Verified working end-to-end

Both conversational flows (symptom → recommendation and renewal) have been run successfully — locally via
`run_agent_cli.py`, through the local FastAPI server, and through the **deployed Lambda Function URL** —
against a live CockroachDB Cloud cluster, the real Managed MCP Server, live Bedrock Titan embeddings, and
the Anthropic API. This isn't just a code review, it produced correct, plan-grounded recommendations at
every layer. The MCP tool schema (`crdb_mcp/mcp_client.py`) reflects the server's *actual* live schema
(retrieved via `MCPClient.list_tools()`), not a guess from docs — see that file's docstring for specifics.

## Gotchas hit while wiring this up (already fixed in code, documented here so they don't get re-debugged)

- **`psycopg2-binary` + macOS**: its bundled libpq doesn't read the system keychain the way `psql` does,
  so `sslrootcert=system` (which fixes `psql`) fails for Python with a cert-verify error. Fixed by passing
  `sslrootcert=certifi.where()` explicitly in `db/seed_data.py`'s `psycopg2.connect()` call instead.
- **MCP server has no parameter binding**: `select_query`/`insert_rows` both take a raw SQL string, no
  `$1`-style params. All values are inlined via `crdb_mcp.mcp_client.sql_literal()`.
- **MCP query length cap (16384 chars)**: a 1024-dim embedding formatted with Python's `repr()` (up to ~17
  sig figs/float) blows past this when inlined into a vector search query. Fixed by formatting to 6
  decimal places in `agent/embeddings.py:to_vector_literal` — plenty of precision for cosine similarity.
- **`mcp` SDK 2.0.0 API**: differs from most examples online (which target the 1.x line) —
  `streamable_http_client` (not `streamablehttp_client`), yields a 2-tuple not 3, and headers go through
  an `httpx2.AsyncClient` (a separate package from `httpx`) passed as `http_client=`.
- **Composing MCP's async context managers**: don't hand-roll `__aenter__`/`__aexit__` chaining across
  instance attributes — the underlying transport uses anyio task groups that require strict same-task
  nesting. Use `contextlib.AsyncExitStack` instead (see `crdb_mcp/mcp_client.py`).
- **Claude's extended thinking + `max_tokens`**: a low `max_tokens` can get exhausted mid-`thinking` block
  (no text yet) *or* mid-final-answer (truncated text, e.g. a comparison table cut off mid-row) — both
  looked like valid responses at first glance. `agent/llm.py` uses `max_tokens=8192` and checks
  `stop_reason == "max_tokens"` *before* anything else, always retrying rather than ever returning
  partial/truncated text.
- **`cryptography` wheel vs. conda's OpenSSL**: pip's `cryptography` wheel can fail to load
  (`symbol not found ... _EVP_DigestSqueeze`) in a fresh conda env due to an OpenSSL version mismatch.
  Fixed by installing `cryptography` from `conda-forge` instead of pip in that env.
- **`api/app.py` needs its own `load_dotenv()`**: unlike the CLI script and seed script, nothing else
  loads `.env` when uvicorn/Lambda import `api.app` fresh — added `load_dotenv()` at the top of the file,
  before the `agent` imports.
- **`psycopg2-binary` doesn't belong in the Lambda image**: it has no arm64 wheel for this Python/manylinux
  combo and fails trying to build from source (`pg_config` not found). Realized it isn't actually needed
  at runtime anyway (only `db/seed_data.py` uses direct SQL) — `infra/requirements-lambda.txt` is a
  runtime-only subset that excludes it (and `certifi`, `uvicorn`, `pytest`).
- **API Gateway HTTP API hard-caps integration timeout at ~30s**, regardless of the Lambda's own configured
  `Timeout`. The renewal flow's multi-turn tool-calling can exceed that. Used a **Lambda Function URL**
  instead (`infra/deploy.py:ensure_function_url`) — it proxies directly to Lambda with no such ceiling.
- **`AWS_REGION` is a reserved Lambda env var** — `CreateFunction`/`UpdateFunctionConfiguration` reject it
  outright if you try to set it yourself. Lambda sets it automatically from the deployed region.
- **Public (`AuthType=NONE`) Function URLs need two resource-policy grants, not one**: since Oct 2025, AWS
  requires both `lambda:InvokeFunctionUrl` *and* `lambda:InvokeFunction` (the latter with
  `InvokedViaFunctionUrl=True`) — granting only the first still 403s every request, and it's not a
  propagation delay (looks identical to one at first).

## Roadmap (not done yet)

- Optional: swap embeddings to Voyage AI if Bedrock Titan access approval is slow.
- Optional: tear down the now-unused `insurance-navigator-api` API Gateway HTTP API resource (superseded
  by the Function URL, harmless but unnecessary cruft left from an earlier deploy attempt).

## License

[MIT](LICENSE)
