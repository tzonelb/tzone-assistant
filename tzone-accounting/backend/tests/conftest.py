from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """A throwaway database and a freshly loaded registry for each test."""
    monkeypatch.setenv("ACCOUNTING_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ACCOUNTING_JWT_SECRET", "test-secret")
    monkeypatch.setenv("ACCOUNTING_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ACCOUNTING_ADMIN_PASSWORD", "admin123")

    from app.core.registry import reset_registry

    reset_registry()
    yield tmp_path
    reset_registry()


@pytest.fixture()
def client(app_env):
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        response = test_client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "admin123",
                "device_id": str(uuid.uuid4()),
                "device_label": "pytest",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        test_client.headers["Authorization"] = f"Bearer {body['token']}"
        test_client.device_id = body["device"]["id"]
        test_client.device_code = body["device"]["device_code"]
        yield test_client


@pytest.fixture()
def push(client):
    """Push (entity, record) pairs with auto-incrementing op sequence numbers."""
    counter = {"seq": 0}

    def _push(ops: list[tuple[str, dict]]) -> dict:
        payload_ops = []
        for entity, record in ops:
            counter["seq"] += 1
            payload_ops.append(
                {
                    "seq": counter["seq"],
                    "entity": entity,
                    "id": record["id"],
                    "op": "upsert",
                    "record": record,
                }
            )
        response = client.post(
            "/api/sync/push", json={"device_id": client.device_id, "ops": payload_ops}
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _push


def entry(entry_id: str, date: str, lines: list[tuple[str, int, int]], **overrides) -> dict:
    """Build a journal entry record from (account_id, debit, credit) tuples."""
    record = {
        "id": entry_id,
        "entry_no": f"JV-{entry_id}",
        "date": date,
        "memo": "test entry",
        "currency": "USD",
        "fx_rate": 1_000_000,
        "status": "posted",
        "source_kind": "manual",
        "lines": [
            {
                "account_id": account_id,
                "debit": debit,
                "credit": credit,
                "base_debit": debit,
                "base_credit": credit,
            }
            for account_id, debit, credit in lines
        ],
        "rev": 1,
        "updated_at": "2026-01-01T00:00:00.000Z",
        "deleted": False,
    }
    record.update(overrides)
    return record


def document(doc_id: str, doc_type: str, **overrides) -> dict:
    record = {
        "id": doc_id,
        "doc_type": doc_type,
        "doc_no": f"{doc_type[:2].upper()}-A7-{doc_id}",
        "date": "2026-03-01",
        "due_date": "2026-03-31",
        "partner_id": None,
        "currency": "USD",
        "fx_rate": 1_000_000,
        "total": 1000,
        "base_total": 1000,
        "status": "posted",
        "payload": {},
        "rev": 1,
        "updated_at": "2026-03-01T10:00:00.000Z",
        "deleted": False,
    }
    record.update(overrides)
    return record
