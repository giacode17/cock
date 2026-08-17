# AWS Lambda Python base image — building here (not a plain zip) matters
# for any native-extension deps (e.g. cryptography, pulled in transitively)
# to get Lambda-compatible wheels instead of fighting macOS/Linux mismatches.
FROM public.ecr.aws/lambda/python:3.11

# infra/requirements-lambda.txt, NOT the top-level requirements.txt: this is
# the runtime-only subset (no psycopg2-binary/uvicorn/pytest — see that
# file's comments for why each is excluded).
COPY infra/requirements-lambda.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY agent ${LAMBDA_TASK_ROOT}/agent
COPY crdb_mcp ${LAMBDA_TASK_ROOT}/crdb_mcp
COPY api ${LAMBDA_TASK_ROOT}/api

# Intentionally NOT copying .env or db/ — secrets come from Lambda environment
# variables (set by infra/deploy.py), and db/ (schema/seed) is an ops-time
# tool that doesn't belong in the runtime image.

CMD ["api.lambda_handler.handler"]
