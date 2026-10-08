"""Real receiver issuance/replay and legacy historical persistence."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.employee_referrals import capture
from src.employee_referrals.models import CampaignReplica
from src.employee_referrals.schemas import utc_text
from tests.test_employee_referral_k1_contract import enabled as enabled
from tests.test_employee_referral_k1_receiver import k1 as k1
from tests.test_employee_referral_k1_receiver import next_event


async def test_new_and_renewed_confirmations_are_300_and_replay_does_not_extend(k1):
    first = await k1.post()
    assert first.status_code == 200
    before = first.json()["registry"]
    assert datetime.fromisoformat(before["valid_until"]) - datetime.fromisoformat(
        before["issued_at"]
    ) == timedelta(seconds=300)
    replay = await k1.post()
    assert replay.json()["replayed"]
    assert replay.json()["registry"]["valid_until"] == before["valid_until"]
    issued = datetime.fromisoformat(k1.data["issued_at"]) + timedelta(seconds=1)
    response = await k1.post(next_event(k1.data, issued_at=utc_text(issued)))
    assert response.json()["receipt"]["outcome"] == "renewed"
    assert datetime.fromisoformat(response.json()["registry"]["valid_until"]) == issued + timedelta(
        seconds=300
    )
    old_replay = await k1.post()
    assert old_replay.json()["receipt"] == first.json()["receipt"]
    assert (
        old_replay.json()["registry"]["valid_until"] == response.json()["registry"]["valid_until"]
    )


async def test_historical_ended_minute_row_and_receipt_replay_are_unchanged(k1):
    ended = next_event(k1.data, revision=2)
    ended["campaign"]["state"] = "ended"
    ended["campaign"]["ended_at"] = ended["issued_at"]
    first = await k1.post(ended)
    assert first.status_code == 200
    async with k1.sessions.begin() as session:
        row = await session.get(CampaignReplica, ended["campaign"]["id"])
        row.valid_until = row.issued_at + timedelta(seconds=60)
    before, _, receipts = await k1.rows()
    deadline = before.valid_until
    replay = await k1.post(ended)
    assert replay.json()["replayed"]
    assert replay.json()["receipt"] == first.json()["receipt"]
    assert datetime.fromisoformat(replay.json()["registry"]["valid_until"]) == deadline
    after, _, after_receipts = await k1.rows()
    assert after.state == "ended"
    assert after.valid_until == deadline
    assert [r.event_id for r in after_receipts] == [r.event_id for r in receipts]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("before-expiry", True),
        ("expiry", False),
        ("after-expiry", False),
        ("before-end", True),
        ("end", False),
        ("after-end", False),
    ],
)
async def test_five_minute_capture_keeps_exact_expiry_and_scheduled_end(
    k1, monkeypatch, case, expected
):
    assert (await k1.post()).status_code == 200
    async with k1.sessions.begin() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        replica = await session.get(CampaignReplica, 1)
        delta = timedelta(microseconds=1)
        if "expiry" in case:
            replica.issued_at = now - timedelta(seconds=300)
            if case == "before-expiry":
                replica.issued_at += delta
            elif case == "after-expiry":
                replica.issued_at -= delta
            replica.valid_until = replica.issued_at + timedelta(seconds=300)
        else:
            replica.end_at = now
            if case == "before-end":
                replica.end_at += delta
            elif case == "after-end":
                replica.end_at -= delta

    async def clock(_session):
        return now

    monkeypatch.setattr(capture, "capture_clock", clock)
    async with k1.sessions() as session:
        current = await capture.current_referral(session, k1.data["participants"][0]["token"])
    assert (current is not None) is expected
