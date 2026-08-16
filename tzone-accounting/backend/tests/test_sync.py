from __future__ import annotations

from conftest import document, entry


def test_health_reports_the_loaded_module_graph(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["modules"] >= 8
    assert body["entities"] >= 5


def test_seed_ran_for_every_module(client):
    status = client.get("/api/sync/status").json()
    assert status["counts"]["account"] > 30      # accounting module's chart
    assert status["counts"]["settings"] == 1     # base module's company row
    assert status["cursor"] > 0


def test_device_code_is_assigned_for_offline_numbering(client):
    assert len(client.device_code) == 2


def test_push_pull_round_trip(push, client):
    result = push(
        [("journal_entry", entry("e1", "2026-03-01", [("acc-1110", 5000, 0), ("acc-4100", 0, 5000)]))]
    )
    assert result["accepted"] == [1]
    assert result["rejected"] == []

    changes = client.get("/api/sync/pull", params={"since": 0, "limit": 2000}).json()
    entries = [c for c in changes["changes"] if c["entity"] == "journal_entry"]
    assert len(entries) == 1
    assert len(entries[0]["record"]["lines"]) == 2
    assert entries[0]["record"]["lines"][0]["base_debit"] == 5000


def test_pull_cursor_only_returns_new_changes(push, client):
    first = client.get("/api/sync/pull", params={"since": 0, "limit": 2000}).json()
    push([("journal_entry", entry("e2", "2026-03-02", [("acc-1110", 100, 0), ("acc-4100", 0, 100)]))])

    second = client.get("/api/sync/pull", params={"since": first["cursor"]}).json()
    assert {change["record"]["id"] for change in second["changes"]} == {"e2"}


def test_unbalanced_entry_is_rejected_with_a_reason(push):
    result = push(
        [("journal_entry", entry("bad", "2026-03-01", [("acc-1110", 5000, 0), ("acc-4100", 0, 4000)]))]
    )
    assert result["accepted"] == []
    assert "unbalanced" in result["rejected"][0]["reason"]


def test_group_account_cannot_be_posted_to(push):
    result = push(
        [("journal_entry", entry("bad2", "2026-03-01", [("acc-1000", 100, 0), ("acc-4100", 0, 100)]))]
    )
    assert "group account" in result["rejected"][0]["reason"]


def test_unknown_account_is_rejected(push):
    result = push(
        [("journal_entry", entry("bad3", "2026-03-01", [("acc-9999", 100, 0), ("acc-4100", 0, 100)]))]
    )
    assert "unknown account" in result["rejected"][0]["reason"]


def test_unknown_entity_is_rejected_not_crashed(client):
    response = client.post(
        "/api/sync/push",
        json={
            "device_id": client.device_id,
            "ops": [{"seq": 1, "entity": "payroll_slip", "id": "x", "record": {}}],
        },
    )
    assert response.status_code == 200
    assert "no installed module owns entity" in response.json()["rejected"][0]["reason"]


def test_one_bad_op_does_not_block_the_batch(push, client):
    result = push(
        [
            ("journal_entry", entry("ok1", "2026-03-01", [("acc-1110", 100, 0), ("acc-4100", 0, 100)])),
            ("journal_entry", entry("bad4", "2026-03-01", [("acc-1110", 100, 0), ("acc-4100", 0, 90)])),
            ("journal_entry", entry("ok2", "2026-03-01", [("acc-1110", 200, 0), ("acc-4100", 0, 200)])),
        ]
    )
    assert sorted(result["accepted"]) == [1, 3]
    assert [r["id"] for r in result["rejected"]] == ["bad4"]

    changes = client.get("/api/sync/pull", params={"since": 0, "limit": 2000}).json()
    stored = {c["record"]["id"] for c in changes["changes"] if c["entity"] == "journal_entry"}
    assert stored == {"ok1", "ok2"}


def test_last_writer_wins_on_updated_at(push, client):
    lines = [("acc-1110", 100, 0), ("acc-4100", 0, 100)]
    push([("journal_entry", entry("e3", "2026-03-01", lines, memo="first", updated_at="2026-03-01T10:00:00.000Z"))])
    # An older write must not overwrite the newer one.
    push([("journal_entry", entry("e3", "2026-03-01", lines, memo="stale", updated_at="2026-03-01T09:00:00.000Z"))])
    push([("journal_entry", entry("e3", "2026-03-01", lines, memo="newer", updated_at="2026-03-01T11:00:00.000Z"))])

    changes = client.get("/api/sync/pull", params={"since": 0, "limit": 2000}).json()
    record = next(c["record"] for c in changes["changes"] if c["record"]["id"] == "e3")
    assert record["memo"] == "newer"


def test_deleting_a_record_replicates_as_a_tombstone(client, push):
    push([("partner", {"id": "p1", "name": "Ali", "kind": "customer",
                       "updated_at": "2026-03-01T10:00:00.000Z", "rev": 1})])
    response = client.post(
        "/api/sync/push",
        json={
            "device_id": client.device_id,
            "ops": [{"seq": 99, "entity": "partner", "id": "p1", "op": "delete",
                     "record": {"name": "Ali", "kind": "customer",
                                "updated_at": "2026-03-02T10:00:00.000Z", "rev": 2}}],
        },
    )
    assert response.status_code == 200
    changes = client.get("/api/sync/pull", params={"since": 0, "limit": 2000}).json()
    record = next(c["record"] for c in changes["changes"] if c["record"]["id"] == "p1")
    assert record["deleted"] is True


def test_locked_period_blocks_back_dated_posting(push):
    from app.db import utcnow

    push(
        [
            (
                "settings",
                {
                    "id": "company",
                    "payload": {"base_currency": "USD", "lock_date": "2026-02-28"},
                    "rev": 2,
                    # Later than the seeded row, or last-writer-wins keeps the seeded settings.
                    "updated_at": utcnow(),
                    "deleted": False,
                },
            )
        ]
    )

    blocked = push(
        [("journal_entry", entry("old", "2026-02-01", [("acc-1110", 100, 0), ("acc-4100", 0, 100)]))]
    )
    assert "locked" in blocked["rejected"][0]["reason"]

    allowed = push(
        [("journal_entry", entry("new", "2026-03-01", [("acc-1110", 100, 0), ("acc-4100", 0, 100)]))]
    )
    assert allowed["rejected"] == []


def test_legal_number_is_assigned_once_and_is_gapless(push):
    result = push(
        [
            ("document", document("d1", "sales_invoice")),
            ("document", document("d2", "sales_invoice", updated_at="2026-03-01T10:00:01.000Z")),
        ]
    )
    assert result["assigned"] == {
        "d1": {"legal_no": "SI-000001"},
        "d2": {"legal_no": "SI-000002"},
    }

    again = push([("document", document("d1", "sales_invoice", updated_at="2026-03-01T12:00:00.000Z"))])
    assert again["assigned"] == {"d1": {"legal_no": "SI-000001"}}


def test_each_document_type_has_its_own_number_series(push):
    result = push(
        [
            ("document", document("s1", "sales_invoice")),
            ("document", document("p1", "purchase_invoice")),
            ("document", document("r1", "receipt")),
        ]
    )
    assert result["assigned"]["s1"]["legal_no"] == "SI-000001"
    assert result["assigned"]["p1"]["legal_no"] == "PI-000001"
    assert result["assigned"]["r1"]["legal_no"] == "RC-000001"


def test_document_of_an_uninstalled_type_is_rejected(push):
    result = push([("document", document("x1", "payroll_run"))])
    assert "no installed module provides document type" in result["rejected"][0]["reason"]


def test_pull_requires_authentication(client):
    client.headers.pop("Authorization")
    assert client.get("/api/sync/pull", params={"since": 0}).status_code == 401
