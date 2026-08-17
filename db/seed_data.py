"""Seed CockroachDB with mock plans, SBC document chunks (+embeddings), a mock
user, and mock visit history. Connects DIRECTLY to CockroachDB (not via MCP) —
seeding is an ops task, not part of the agent's runtime tool set.

Run after applying db/schema.sql, e.g.:
    cockroach sql --url "$COCKROACHDB_CONNECTION_STRING" -f db/schema.sql
    python db/seed_data.py
"""
import datetime as dt
import os
import re
import sys
from pathlib import Path

import certifi
import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import embeddings  # noqa: E402
from agent.constants import MOCK_USER_ID  # noqa: E402

load_dotenv()

HERE = Path(__file__).resolve().parent
SBC_DIR = HERE / "mock_sbc_texts"

PLANS = [
    {
        "key": "bronze_hdhp",
        "plan_name": "Summit Bronze HDHP",
        "carrier": "Summit Health Alliance",
        "plan_year": 2026,
        "deductible_individual": 3500.00,
        "deductible_family": 7000.00,
        "oop_max_individual": 7000.00,
        "copay_pcp": 0.00,
        "copay_urgent_care": 0.00,
        "copay_er": 0.00,
        "copay_telehealth": 0.00,
        "coinsurance_pct": 20.00,
        "network_type": "HDHP",
        "sbc_file": "plan_bronze_hdhp.txt",
        "source_doc_name": "SBC_2026_SummitBronzeHDHP.pdf",
    },
    {
        "key": "gold_ppo",
        "plan_name": "Blue Horizon PPO Gold",
        "carrier": "Blue Horizon",
        "plan_year": 2026,
        "deductible_individual": 500.00,
        "deductible_family": 1000.00,
        "oop_max_individual": 4000.00,
        "copay_pcp": 25.00,
        "copay_urgent_care": 60.00,
        "copay_er": 350.00,
        "copay_telehealth": 10.00,
        "coinsurance_pct": 10.00,
        "network_type": "PPO",
        "sbc_file": "plan_gold_ppo.txt",
        "source_doc_name": "SBC_2026_BlueHorizonPPOGold.pdf",
    },
    {
        "key": "silver_hmo",
        "plan_name": "Cascade Silver HMO",
        "carrier": "Cascade Care Network",
        "plan_year": 2026,
        "deductible_individual": 1500.00,
        "deductible_family": 3000.00,
        "oop_max_individual": 5500.00,
        "copay_pcp": 30.00,
        "copay_urgent_care": 75.00,
        "copay_er": 400.00,
        "copay_telehealth": 15.00,
        "coinsurance_pct": 15.00,
        "network_type": "HMO",
        "sbc_file": "plan_silver_hmo.txt",
        "source_doc_name": "SBC_2026_CascadeSilverHMO.pdf",
    },
]


def mock_visits(months_ago: list[int]) -> list[dict]:
    """5-6 mock visits against the mock user's current plan (bronze_hdhp),
    spread over the past several months, deliberately patterned so the
    renewal flow has something real to reason about (frequent, costly ER
    use that a copay-based plan like Blue Horizon Gold would have made
    cheaper)."""
    now = dt.datetime.now(dt.timezone.utc)
    templates = [
        dict(symptom_description="Sharp abdominal pain, went to the ER", recommended_care_type="er",
             estimated_cost_tier="$$$", estimated_cost_low=1200, estimated_cost_high=2500,
             actual_care_type="er", actual_cost=2100.00, satisfaction_rating=3, status="completed"),
        dict(symptom_description="Twisted ankle over the weekend, ER since it was Sunday night", recommended_care_type="er",
             estimated_cost_tier="$$$", estimated_cost_low=900, estimated_cost_high=1800,
             actual_care_type="er", actual_cost=1450.00, satisfaction_rating=2, status="completed"),
        dict(symptom_description="Sore throat and fever for 2 days", recommended_care_type="telehealth",
             estimated_cost_tier="$", estimated_cost_low=0, estimated_cost_high=50,
             actual_care_type="telehealth", actual_cost=0.00, satisfaction_rating=5, status="completed"),
        dict(symptom_description="Minor cut needing stitches", recommended_care_type="urgent_care",
             estimated_cost_tier="$$", estimated_cost_low=150, estimated_cost_high=300,
             actual_care_type="urgent_care", actual_cost=210.00, satisfaction_rating=4, status="completed"),
        dict(symptom_description="Annual checkup reminder", recommended_care_type="pcp",
             estimated_cost_tier="$", estimated_cost_low=0, estimated_cost_high=150,
             actual_care_type="pcp", actual_cost=120.00, satisfaction_rating=4, status="completed"),
        dict(symptom_description="Persistent cough, unsure if urgent", recommended_care_type="urgent_care",
             estimated_cost_tier="$$", estimated_cost_low=100, estimated_cost_high=250,
             actual_care_type=None, actual_cost=None, satisfaction_rating=None, status="recommended"),
    ]
    visits = []
    for template, months in zip(templates, months_ago):
        row = dict(template)
        row["created_at"] = now - dt.timedelta(days=30 * months)
        visits.append(row)
    return visits


def chunk_text(text: str, max_chars: int = 700) -> list[str]:
    """Split on blank-line paragraph boundaries, packing up to max_chars per chunk."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def main():
    # sslrootcert=certifi.where(): psycopg2-binary bundles its own libpq/openssl,
    # which doesn't read the macOS system keychain the way `psql` does — certifi's
    # portable CA bundle sidesteps that instead of requiring a downloaded cert file.
    conn = psycopg2.connect(os.environ["COCKROACHDB_CONNECTION_STRING"], sslrootcert=certifi.where())
    conn.autocommit = True
    cur = conn.cursor()

    print(f"Seeding mock user {MOCK_USER_ID} ...")
    cur.execute(
        "INSERT INTO users (user_id, display_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
        (MOCK_USER_ID, "Jordan (Demo User)"),
    )

    plan_ids = {}
    for plan in PLANS:
        cur.execute(
            """
            INSERT INTO insurance_plans
                (plan_name, carrier, plan_year, deductible_individual, deductible_family,
                 oop_max_individual, copay_pcp, copay_urgent_care, copay_er, copay_telehealth,
                 coinsurance_pct, network_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING plan_id
            """,
            (
                plan["plan_name"], plan["carrier"], plan["plan_year"],
                plan["deductible_individual"], plan["deductible_family"], plan["oop_max_individual"],
                plan["copay_pcp"], plan["copay_urgent_care"], plan["copay_er"], plan["copay_telehealth"],
                plan["coinsurance_pct"], plan["network_type"],
            ),
        )
        plan_id = cur.fetchone()[0]
        plan_ids[plan["key"]] = plan_id
        print(f"  inserted plan {plan['plan_name']} -> {plan_id}")

        text = (SBC_DIR / plan["sbc_file"]).read_text()
        chunks = chunk_text(text)
        print(f"    embedding {len(chunks)} chunks ...")
        for i, chunk in enumerate(chunks):
            vector = embeddings.embed_text(chunk)
            cur.execute(
                """
                INSERT INTO plan_document_chunks
                    (plan_id, source_doc_name, chunk_text, chunk_index, embedding)
                VALUES (%s, %s, %s, %s, %s::VECTOR)
                """,
                (plan_id, plan["source_doc_name"], chunk, i, embeddings.to_vector_literal(vector)),
            )

    # Enroll the mock user in the first plan (bronze_hdhp) as their current plan.
    current_plan_id = plan_ids["bronze_hdhp"]
    cur.execute(
        """
        INSERT INTO user_plan_enrollments (user_id, plan_id, plan_year, is_current)
        VALUES (%s, %s, %s, true)
        """,
        (MOCK_USER_ID, current_plan_id, 2026),
    )

    print("Seeding mock visits ...")
    for visit in mock_visits(months_ago=[5, 4, 3, 2, 1, 0]):
        cur.execute(
            """
            INSERT INTO visits
                (user_id, plan_id, symptom_description, recommended_care_type, estimated_cost_tier,
                 estimated_cost_low, estimated_cost_high, actual_care_type, actual_cost,
                 satisfaction_rating, status, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING visit_id
            """,
            (
                MOCK_USER_ID, current_plan_id, visit["symptom_description"], visit["recommended_care_type"],
                visit["estimated_cost_tier"], visit["estimated_cost_low"], visit["estimated_cost_high"],
                visit["actual_care_type"], visit["actual_cost"], visit["satisfaction_rating"],
                visit["status"], visit["created_at"], visit["created_at"],
            ),
        )
        visit_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO conversation_messages (user_id, visit_id, role, content, created_at) VALUES (%s,%s,%s,%s,%s)",
            (MOCK_USER_ID, visit_id, "user", visit["symptom_description"], visit["created_at"]),
        )

    print("Done.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
