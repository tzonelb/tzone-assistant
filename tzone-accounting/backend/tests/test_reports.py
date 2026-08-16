from __future__ import annotations

from conftest import document, entry

# A small but complete month: capital in, a sale on credit, a collection, rent paid.
BOOKS = [
    ("cap", "2026-03-01", [("acc-1120", 1_000_00, 0), ("acc-3100", 0, 1_000_00)]),
    ("sale", "2026-03-05", [("acc-1130", 500_00, 0), ("acc-4100", 0, 500_00)]),
    ("collect", "2026-03-10", [("acc-1120", 300_00, 0), ("acc-1130", 0, 300_00)]),
    ("rent", "2026-03-15", [("acc-5300", 120_00, 0), ("acc-1120", 0, 120_00)]),
]


def _load(push):
    push([("journal_entry", entry(eid, date, lines)) for eid, date, lines in BOOKS])


def test_trial_balance_balances(push, client):
    _load(push)
    report = client.get(
        "/api/reports/trial-balance", params={"from": "2026-03-01", "to": "2026-03-31"}
    ).json()
    assert report["balanced"] is True
    assert report["total_debit"] == report["total_credit"] == 1_920_00


def test_profit_and_loss(push, client):
    _load(push)
    report = client.get(
        "/api/reports/profit-and-loss", params={"from": "2026-03-01", "to": "2026-03-31"}
    ).json()
    assert report["total_income"] == 500_00
    assert report["total_expense"] == 120_00
    assert report["net_profit"] == 380_00


def test_balance_sheet_balances_and_includes_retained_earnings(push, client):
    _load(push)
    report = client.get("/api/reports/balance-sheet", params={"as_of": "2026-03-31"}).json()
    assert report["balanced"] is True
    # bank 1000 + 300 - 120 = 1180, receivable 500 - 300 = 200
    assert report["total_assets"] == 1_380_00
    assert report["retained_earnings"] == 380_00
    assert report["total_equity"] == 1_380_00


def test_general_ledger_running_balance(push, client):
    _load(push)
    report = client.get(
        "/api/reports/general-ledger",
        params={"account_id": "acc-1120", "from": "2026-03-01", "to": "2026-03-31"},
    ).json()
    assert report["opening"] == 0
    assert [line["balance"] for line in report["lines"]] == [1_000_00, 1_300_00, 1_180_00]
    assert report["closing"] == 1_180_00


def test_general_ledger_opening_excludes_the_period(push, client):
    _load(push)
    report = client.get(
        "/api/reports/general-ledger",
        params={"account_id": "acc-1120", "from": "2026-03-10", "to": "2026-03-31"},
    ).json()
    assert report["opening"] == 1_000_00
    assert report["closing"] == 1_180_00


def test_draft_entries_stay_out_of_the_reports(push, client):
    push(
        [
            ("journal_entry", entry("d1", "2026-03-01", [("acc-1110", 999_00, 0), ("acc-4100", 0, 999_00)], status="draft")),
        ]
    )
    report = client.get(
        "/api/reports/profit-and-loss", params={"from": "2026-03-01", "to": "2026-03-31"}
    ).json()
    assert report["total_income"] == 0


def test_report_period_filters_out_other_months(push, client):
    _load(push)
    report = client.get(
        "/api/reports/profit-and-loss", params={"from": "2026-04-01", "to": "2026-04-30"}
    ).json()
    assert report["net_profit"] == 0


def test_receivable_aging_buckets_by_due_date(push, client):
    invoice = document(
        "inv-late",
        "sales_invoice",
        date="2026-01-10",
        due_date="2026-01-20",
        total=400_00,
        base_total=400_00,
        updated_at="2026-01-10T00:00:00.000Z",
    )
    receipt = document(
        "rc-1",
        "receipt",
        date="2026-02-01",
        due_date=None,
        total=150_00,
        base_total=150_00,
        payload={"allocations": [{"document_id": "inv-late", "base_amount": 150_00}]},
        updated_at="2026-02-01T00:00:00.000Z",
    )
    push([("document", invoice), ("document", receipt)])

    report = client.get(
        "/api/documents/aging", params={"kind": "receivable", "as_of": "2026-03-01"}
    ).json()
    assert report["total"] == 250_00           # 400 invoiced - 150 collected
    assert report["buckets"]["d31_60"] == 250_00
    assert report["items"][0]["days_late"] == 40


def test_fully_settled_invoice_drops_out_of_aging(push, client):
    invoice = document("inv-paid", "sales_invoice", date="2026-01-10", due_date="2026-01-20",
                       total=100_00, base_total=100_00, updated_at="2026-01-10T00:00:00.000Z")
    receipt = document("rc-2", "receipt", date="2026-02-01", total=100_00, base_total=100_00,
                       payload={"allocations": [{"document_id": "inv-paid", "base_amount": 100_00}]},
                       updated_at="2026-02-01T00:00:00.000Z")
    push([("document", invoice), ("document", receipt)])

    report = client.get(
        "/api/documents/aging", params={"kind": "receivable", "as_of": "2026-03-01"}
    ).json()
    assert report["items"] == []


def test_payable_aging_uses_purchase_invoices_and_payments(push, client):
    push(
        [
            ("document", document("pi-1", "purchase_invoice", date="2026-02-01",
                                  due_date="2026-02-10", total=600_00, base_total=600_00,
                                  updated_at="2026-02-01T00:00:00.000Z")),
            ("document", document("pm-1", "payment", date="2026-02-20", total=200_00,
                                  base_total=200_00,
                                  payload={"allocations": [{"document_id": "pi-1", "base_amount": 200_00}]},
                                  updated_at="2026-02-20T00:00:00.000Z")),
        ]
    )
    report = client.get(
        "/api/documents/aging", params={"kind": "payable", "as_of": "2026-03-01"}
    ).json()
    assert report["total"] == 400_00
    # A receipt must not settle a payable, and vice versa.
    receivable = client.get(
        "/api/documents/aging", params={"kind": "receivable", "as_of": "2026-03-01"}
    ).json()
    assert receivable["total"] == 0
