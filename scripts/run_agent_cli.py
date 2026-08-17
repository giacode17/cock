"""Local test harness for the agent — no API/frontend needed.

Usage:
    python scripts/run_agent_cli.py "sharp pain in my lower right side"
    python scripts/run_agent_cli.py --renewal
    python scripts/run_agent_cli.py --user-id <uuid> "a symptom description"
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent import orchestrator  # noqa: E402
from agent.constants import MOCK_USER_ID  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symptom", nargs="?", help="Symptom/situation description")
    parser.add_argument("--renewal", action="store_true", help="Run the renewal-recommendation flow instead")
    parser.add_argument("--user-id", default=MOCK_USER_ID, help="Override the mock user id")
    args = parser.parse_args()

    if args.renewal:
        reply = await orchestrator.recommend_renewal(args.user_id)
    else:
        if not args.symptom:
            parser.error("provide a symptom description, or pass --renewal")
        reply = await orchestrator.recommend_for_symptom(args.user_id, args.symptom)

    print(reply)


if __name__ == "__main__":
    asyncio.run(main())
