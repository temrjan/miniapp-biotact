"""Actual read-only source/replica SQL against isolated synthetic PostgreSQL."""

# Commands use the fixed owned container and synthetic SQL only.
# ruff: noqa: S607

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops/tls-remediation"))
import observe  # noqa: E402

sql = (
    """BEGIN;
CREATE TABLE campaigns(id bigint, state text,start_at timestamptz,end_at timestamptz);
CREATE TABLE referral_campaign_registries(campaign_id bigint,content_revision bigint,content_sha256 text,participant_count bigint,confirmed_at timestamptz,confirmed_ack jsonb,blocked_reason text);
CREATE TABLE referral_k1_jobs(campaign_id bigint,state text);
CREATE TABLE employee_referral_campaigns(campaign_id bigint,content_revision bigint,content_sha256 text,issued_at timestamptz,valid_until timestamptz,state text,ended_at timestamptz,start_at timestamptz,end_at timestamptz);
CREATE TABLE employee_referral_tokens(campaign_id bigint,in_current_registry boolean);
CREATE TABLE employee_referral_k1_receipts(campaign_id bigint,content_revision bigint,content_sha256 text,issued_at timestamptz,outcome text);
INSERT INTO campaigns VALUES(7,'published',now()-interval '1 day',now()+interval '1 day');
INSERT INTO referral_campaign_registries VALUES(7,1,'abc',1,now(),jsonb_build_object('registry',jsonb_build_object('content_revision','1','content_sha256','abc','participant_count',1,'state','published','issued_at',now(),'valid_until',now()+interval '300 seconds')),NULL);
INSERT INTO employee_referral_campaigns VALUES(7,1,'abc',now(),now()+interval '300 seconds','published',NULL,now()-interval '1 day',now()+interval '1 day');
INSERT INTO employee_referral_tokens VALUES(7,true);
INSERT INTO employee_referral_k1_receipts VALUES(7,1,'abc',now(),'renewed');
COMMIT;
BEGIN READ ONLY;
"""
    + observe.SOURCE_SQL
    + observe.REPLICA_SQL
    + "ROLLBACK;"
)
result = subprocess.run(
    [
        "podman",
        "exec",
        "-i",
        "tls-f30-postgres-r1",
        "psql",
        "-U",
        "tls_fixture",
        "-d",
        "tls_k1_r2",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
    ],
    input=sql,
    text=True,
    capture_output=True,
    timeout=20,
    check=True,
)
rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
assert len(rows) == 2
assert set(rows[0]) == set(rows[1]) == {"7"}
assert rows[0]["7"]["ack_matches"] is True
assert rows[1]["7"]["receipt_matches"] is True
assert rows[0]["7"]["participants"] == rows[1]["7"]["participants"] == 1
print(
    json.dumps(
        {
            "source": rows[0],
            "replica": rows[1],
            "scope": "synthetic tokenless schema matching actual source/replica columns; both queries in READ ONLY transaction",
        },
        indent=2,
    )
)
print("REAL_K1_SOURCE_REPLICA_SQL_PASSED")
