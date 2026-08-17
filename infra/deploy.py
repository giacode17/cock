"""Deploy the Insurance Navigator Agent to AWS Lambda + API Gateway (HTTP API).

Container-image Lambda, not a zip — see infra/Dockerfile for why. Drives
everything through boto3 (ECR, IAM, Lambda, API Gateway v2) plus the `docker`
CLI via subprocess for the actual image build/push (boto3 has no build step).

Requires:
  - Docker Desktop running locally
  - AWS credentials in .env (BEDROCK_ACCESSKEY/BEDROCK_SECRET_ACCESSKEY) with
    broad enough permissions for ECR/Lambda/IAM/API Gateway (e.g.
    AdministratorAccess — this is a hackathon deploy script, not meant to be
    least-privilege)

Run: python infra/deploy.py
Idempotent: safe to re-run — creates resources if missing, updates them if
they already exist (e.g. to push a new image after a code change).
"""
import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import boto3
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROJECT = "insurance-navigator"
REGION = os.getenv("AWS_REGION", "us-west-2")
ECR_REPO_NAME = PROJECT
LAMBDA_FUNCTION_NAME = f"{PROJECT}-agent"
ROLE_NAME = f"{PROJECT}-lambda-role"
API_NAME = f"{PROJECT}-api"

# Runtime env vars the Lambda function needs — mirrors .env, minus
# COCKROACHDB_CONNECTION_STRING (only used by ops-time db/seed_data.py, not
# the deployed agent) to keep the deployed secret surface minimal. AWS_REGION
# is also excluded: it's a reserved Lambda env var that AWS sets
# automatically from the function's deployed region — setting it manually is
# rejected outright by CreateFunction/UpdateFunctionConfiguration.
RUNTIME_ENV_KEYS = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "CRDB_MCP_SERVER_URL",
    "CRDB_MCP_CLUSTER_ID",
    "CRDB_MCP_AUTH_TOKEN",
    "BEDROCK_ACCESSKEY",
    "BEDROCK_SECRET_ACCESSKEY",
    "MOCK_USER_ID",
]

session = boto3.Session(
    aws_access_key_id=os.environ["BEDROCK_ACCESSKEY"],
    aws_secret_access_key=os.environ["BEDROCK_SECRET_ACCESSKEY"],
    region_name=REGION,
)
sts = session.client("sts")
ecr = session.client("ecr")
iam = session.client("iam")
lambda_client = session.client("lambda")


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def ensure_ecr_repo() -> str:
    try:
        resp = ecr.create_repository(repositoryName=ECR_REPO_NAME)
        print(f"Created ECR repo {ECR_REPO_NAME}")
        return resp["repository"]["repositoryUri"]
    except ecr.exceptions.RepositoryAlreadyExistsException:
        resp = ecr.describe_repositories(repositoryNames=[ECR_REPO_NAME])
        return resp["repositories"][0]["repositoryUri"]


def build_and_push_image(repo_uri: str) -> str:
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    registry = auth["proxyEndpoint"]

    login = subprocess.run(
        ["docker", "login", "--username", user, "--password-stdin", registry],
        input=password.encode(),
        check=True,
    )
    del login  # just needs to succeed (raises on non-zero exit)

    image_uri = f"{repo_uri}:latest"
    # arm64, not amd64: builds natively on Apple Silicon (no QEMU emulation),
    # and Lambda supports arm64 container images directly — see Architectures=
    # in ensure_lambda_function below, which must match this.
    run(["docker", "build", "--platform", "linux/arm64", "-t", image_uri, "-f", str(ROOT / "infra" / "Dockerfile"), str(ROOT)])
    run(["docker", "push", image_uri])
    return image_uri


TRUST_POLICY = """{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}"""


def ensure_execution_role() -> str:
    try:
        resp = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=TRUST_POLICY)
        role_arn = resp["Role"]["Arn"]
        iam.attach_role_policy(
            RoleName=ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print(f"Created IAM role {ROLE_NAME}, waiting for propagation...")
        time.sleep(10)  # new roles aren't immediately assumable by Lambda
        return role_arn
    except iam.exceptions.EntityAlreadyExistsException:
        return iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]


def ensure_lambda_function(image_uri: str, role_arn: str) -> str:
    env_vars = {k: os.environ[k] for k in RUNTIME_ENV_KEYS if k in os.environ}
    try:
        resp = lambda_client.create_function(
            FunctionName=LAMBDA_FUNCTION_NAME,
            PackageType="Image",
            Code={"ImageUri": image_uri},
            Role=role_arn,
            Timeout=120,
            MemorySize=1024,
            Architectures=["arm64"],
            Environment={"Variables": env_vars},
        )
        print(f"Created Lambda function {LAMBDA_FUNCTION_NAME}")
        function_arn = resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        lambda_client.update_function_code(FunctionName=LAMBDA_FUNCTION_NAME, ImageUri=image_uri)
        lambda_client.get_waiter("function_updated").wait(FunctionName=LAMBDA_FUNCTION_NAME)
        resp = lambda_client.update_function_configuration(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Role=role_arn,
            Timeout=120,
            MemorySize=1024,
            Environment={"Variables": env_vars},
        )
        print(f"Updated Lambda function {LAMBDA_FUNCTION_NAME}")
        function_arn = resp["FunctionArn"]

    lambda_client.get_waiter("function_active_v2").wait(FunctionName=LAMBDA_FUNCTION_NAME)
    return function_arn


def ensure_function_url() -> str:
    """A Lambda Function URL, not API Gateway: HTTP API integrations hard-cap
    at ~29-30s regardless of the Lambda's own configured Timeout, which the
    renewal flow's multi-turn tool-calling can exceed. Function URLs proxy
    directly to Lambda with no such ceiling — the function's own Timeout
    (120s, set in ensure_lambda_function) is what actually applies."""
    try:
        resp = lambda_client.create_function_url_config(
            FunctionName=LAMBDA_FUNCTION_NAME,
            AuthType="NONE",
            Cors={"AllowOrigins": ["*"], "AllowMethods": ["*"], "AllowHeaders": ["*"]},
        )
        print("Created Function URL")
    except lambda_client.exceptions.ResourceConflictException:
        resp = lambda_client.get_function_url_config(FunctionName=LAMBDA_FUNCTION_NAME)
        print("Function URL already exists")

    try:
        lambda_client.add_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId="function-url-invoke",
            Action="lambda:InvokeFunctionUrl",
            Principal="*",
            FunctionUrlAuthType="NONE",
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass  # permission already granted from a previous deploy

    return resp["FunctionUrl"].rstrip("/")

    return api["ApiEndpoint"]


def main():
    print(f"Account: {sts.get_caller_identity()['Account']}, region: {REGION}")

    repo_uri = ensure_ecr_repo()
    image_uri = build_and_push_image(repo_uri)
    role_arn = ensure_execution_role()
    ensure_lambda_function(image_uri, role_arn)
    endpoint = ensure_function_url()

    print("\n=== Deployed ===")
    print(f"API endpoint: {endpoint}")
    print(f"Try: curl {endpoint}/health")
    print(f"Chat:  curl -X POST {endpoint}/chat -H 'Content-Type: application/json' -d '{{\"user_id\":\"{os.environ.get('MOCK_USER_ID')}\",\"message\":\"sharp pain in my side\"}}'")
    print(f"\nUpdate frontend/app.js API_BASE to: {endpoint}")


if __name__ == "__main__":
    main()
