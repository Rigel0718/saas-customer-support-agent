from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app import seed
from app.models.customer import Payment, Subscription


@pytest.mark.parametrize("year", [2026, 2030])
def test_seed_dates_are_relative_to_reference_time(year):
    reference_time = datetime(year, 3, 1, tzinfo=timezone.utc)
    manifest = dict(seed.build_manifest(reference_time))
    subscriptions = {row["id"]: row for row in manifest[Subscription]}
    payments = {row["id"]: row for row in manifest[Payment]}

    assert subscriptions[seed.uid(0x501)]["expires_at"] == (
        reference_time + timedelta(days=30)
    )
    assert subscriptions[seed.uid(0x502)]["expires_at"] == (
        reference_time - timedelta(days=50)
    )
    assert subscriptions[seed.uid(0x503)]["expires_at"] is None
    for row in payments.values():
        assert row["created_at"] in (
            reference_time,
            reference_time - timedelta(days=30),
        )
        if row["status"] == "succeeded":
            assert row["paid_at"] == row["created_at"]
        else:
            assert row["paid_at"] is None


def test_apply_seed_captures_current_utc_on_each_execution(monkeypatch):
    build_manifest = MagicMock(wraps=seed.build_manifest)
    monkeypatch.setattr(seed, "build_manifest", build_manifest)

    for _ in range(2):
        before = datetime.now(timezone.utc)
        seed.apply_seed(MagicMock())
        after = datetime.now(timezone.utc)
        reference_time = build_manifest.call_args.args[0]
        assert before <= reference_time <= after
        assert reference_time.tzinfo == timezone.utc
    assert build_manifest.call_count == 2
