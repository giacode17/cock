"""AWS Lambda entry point — stub for phase 2.

Not wired up or deployed yet (see plan: local script + API first). Once
ready, package this with dependencies (e.g. via AWS SAM/CDK or a container
image, since the anthropic/boto3/psycopg2/mcp deps exceed the plain zip
size limits comfortably handled by container-based Lambda deploys) and
point API Gateway at `handler`.
"""
from mangum import Mangum

from api.app import app

handler = Mangum(app)
