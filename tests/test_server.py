"""
E4-7 — Tests del servidor FastAPI con httpx + anyio.

Fixtures:
  client        → AsyncClient contra 127.0.0.1 con DedupStore temporal
  foreign_client → AsyncClient desde IP externa (para tests de 403)
  signed_payload → función helper que genera un payload HMAC-firmado

Nota: los tests resetean el estado del módulo (queue, kill_switch, dedup)
en cada fixture para garantizar aislamiento total.
"""
import asyncio

import httpx
import pytest
from httpx import ASGITransport

import src.server.server as srv
from src.store.dedup_store import DedupStore, Status
from src.utils.hmac_utils import sign

TEST_SECRET = "test-secret-for-e4-tests-xxxxxxxx"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dedup(tmp_path):
    store = DedupStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
async def client(tmp_dedup):
    """Cliente en 127.0.0.1 con estado del servidor reseteado."""
    srv._dedup = tmp_dedup
    srv._queue = asyncio.Queue()
    srv._kill_switch = False
    srv.HMAC_SECRET = TEST_SECRET

    async with httpx.AsyncClient(
        transport=ASGITransport(app=srv.app),
        base_url="http://127.0.0.1:8080",
    ) as c:
        yield c


@pytest.fixture
async def foreign_client():
    """Cliente desde IP externa — debe recibir 403 en todos los endpoints."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=srv.app, client=("10.0.0.1", 9999)),
        base_url="http://10.0.0.1:8080",
    ) as c:
        yield c


@pytest.fixture
def signed_payload():
    """Devuelve una función que construye payloads HMAC-firmados."""
    def _make(signal_id="sig-test-001", action="SELL", symbol="XAUUSD",
               sl=5189.0, tps=None, dry_run=True):
        payload = {
            "signal_id": signal_id,
            "timestamp": "2026-03-05T10:00:00Z",
            "raw_message": "SELL XAUUSD 5181-5185 / SL 5189 / TP 5179",
            "source_channel": "-1003224347994",
            "action": action,
            "symbol": symbol,
            "entry": {"type": "RANGE", "price": None, "range_low": 5181.0, "range_high": 5185.0},
            "sl": sl,
            "tps": tps or [5179.0, 5177.0],
            "dry_run": dry_run,
            "hmac_sha256": "",
        }
        payload["hmac_sha256"] = sign(payload, TEST_SECRET)
        return payload
    return _make


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["queue_size"] == 0
    assert data["kill_switch"] is False


@pytest.mark.anyio
async def test_health_reflects_queue_size(client, signed_payload):
    await client.post("/api/v1/signal", json=signed_payload("sig-q1"))
    await client.post("/api/v1/signal", json=signed_payload("sig-q2"))
    r = await client.get("/health")
    assert r.json()["queue_size"] == 2


# ---------------------------------------------------------------------------
# POST /api/v1/signal
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_signal_accepted(client, signed_payload):
    r = await client.post("/api/v1/signal", json=signed_payload())
    assert r.status_code == 200
    assert r.json()["queued"] is True


@pytest.mark.anyio
async def test_signal_duplicate_returns_409(client, signed_payload):
    payload = signed_payload()
    await client.post("/api/v1/signal", json=payload)
    r = await client.post("/api/v1/signal", json=payload)
    assert r.status_code == 409
    assert r.json()["detail"] == "duplicate_signal"


@pytest.mark.anyio
async def test_signal_invalid_hmac_returns_401(client, signed_payload):
    payload = signed_payload()
    payload["hmac_sha256"] = "0" * 64   # firma incorrecta
    r = await client.post("/api/v1/signal", json=payload)
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_hmac"


@pytest.mark.anyio
async def test_signal_kill_switch_returns_503(client, signed_payload):
    await client.post("/admin/kill-switch")
    r = await client.post("/api/v1/signal", json=signed_payload())
    assert r.status_code == 503
    assert r.json()["detail"] == "kill_switch_active"


@pytest.mark.anyio
async def test_signal_increments_queue(client, signed_payload):
    for i in range(3):
        await client.post("/api/v1/signal", json=signed_payload(f"sig-{i}"))
    assert srv._queue.qsize() == 3


# ---------------------------------------------------------------------------
# GET /api/v1/pending-signal
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_pending_signal_empty_returns_204(client):
    r = await client.get("/api/v1/pending-signal")
    assert r.status_code == 204


@pytest.mark.anyio
async def test_pending_signal_returns_queued_signal(client, signed_payload):
    payload = signed_payload("sig-pending")
    await client.post("/api/v1/signal", json=payload)

    r = await client.get("/api/v1/pending-signal")
    assert r.status_code == 200
    assert r.json()["signal_id"] == "sig-pending"


@pytest.mark.anyio
async def test_pending_signal_updates_status_to_pending(client, signed_payload):
    await client.post("/api/v1/signal", json=signed_payload("sig-status"))
    await client.get("/api/v1/pending-signal")
    assert srv._dedup.get_status("sig-status") == Status.PENDING


@pytest.mark.anyio
async def test_pending_signal_dequeues_fifo(client, signed_payload):
    for i in range(3):
        await client.post("/api/v1/signal", json=signed_payload(f"sig-fifo-{i}"))

    ids = []
    for _ in range(3):
        r = await client.get("/api/v1/pending-signal")
        ids.append(r.json()["signal_id"])

    assert ids == ["sig-fifo-0", "sig-fifo-1", "sig-fifo-2"]


@pytest.mark.anyio
async def test_pending_signal_kill_switch_returns_503(client, signed_payload):
    await client.post("/api/v1/signal", json=signed_payload())
    await client.post("/admin/kill-switch")
    r = await client.get("/api/v1/pending-signal")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/v1/confirm
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_confirm_executed(client, signed_payload):
    await client.post("/api/v1/signal", json=signed_payload("sig-confirm"))
    await client.get("/api/v1/pending-signal")   # → status PENDING

    r = await client.post("/api/v1/confirm", json={
        "signal_id": "sig-confirm",
        "status": "executed",
        "order_ticket": 987654,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "executed"
    assert srv._dedup.get_status("sig-confirm") == Status.EXECUTED


@pytest.mark.anyio
async def test_confirm_failed(client, signed_payload):
    await client.post("/api/v1/signal", json=signed_payload("sig-fail"))
    await client.get("/api/v1/pending-signal")

    r = await client.post("/api/v1/confirm", json={
        "signal_id": "sig-fail",
        "status": "failed",
    })
    assert r.status_code == 200
    assert srv._dedup.get_status("sig-fail") == Status.FAILED


@pytest.mark.anyio
async def test_confirm_unknown_signal_returns_404(client):
    r = await client.post("/api/v1/confirm", json={
        "signal_id": "sig-ghost",
        "status": "executed",
    })
    assert r.status_code == 404


@pytest.mark.anyio
async def test_confirm_invalid_status_returns_422(client, signed_payload):
    await client.post("/api/v1/signal", json=signed_payload("sig-bad-status"))
    r = await client.post("/api/v1/confirm", json={
        "signal_id": "sig-bad-status",
        "status": "invalid_value",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# E4-6 — Kill switch y resume
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_kill_switch_activate(client):
    r = await client.post("/admin/kill-switch")
    assert r.status_code == 200
    assert r.json()["kill_switch"] is True
    assert srv._kill_switch is True


@pytest.mark.anyio
async def test_kill_switch_resume(client):
    await client.post("/admin/kill-switch")
    r = await client.post("/admin/resume")
    assert r.status_code == 200
    assert r.json()["kill_switch"] is False
    assert srv._kill_switch is False


@pytest.mark.anyio
async def test_health_reflects_kill_switch(client):
    await client.post("/admin/kill-switch")
    r = await client.get("/health")
    assert r.json()["kill_switch"] is True


@pytest.mark.anyio
async def test_resume_allows_signals_again(client, signed_payload):
    await client.post("/admin/kill-switch")
    await client.post("/admin/resume")
    r = await client.post("/api/v1/signal", json=signed_payload())
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# E4-2 — Restricción 127.0.0.1
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_foreign_ip_rejected_on_signal(foreign_client, signed_payload):
    r = await foreign_client.post("/api/v1/signal", json=signed_payload())
    assert r.status_code == 403


@pytest.mark.anyio
async def test_foreign_ip_rejected_on_health(foreign_client):
    r = await foreign_client.get("/health")
    assert r.status_code == 403


@pytest.mark.anyio
async def test_foreign_ip_rejected_on_pending(foreign_client):
    r = await foreign_client.get("/api/v1/pending-signal")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# hmac_utils — tests unitarios independientes del servidor
# ---------------------------------------------------------------------------

def test_sign_and_verify_roundtrip():
    payload = {"signal_id": "x", "action": "BUY", "sl": 1.08}
    from src.utils.hmac_utils import sign, verify
    payload["hmac_sha256"] = sign(payload, "secret")
    assert verify(payload, "secret") is True


def test_verify_wrong_secret_fails():
    payload = {"signal_id": "x", "action": "BUY", "sl": 1.08}
    from src.utils.hmac_utils import sign, verify
    payload["hmac_sha256"] = sign(payload, "correct-secret")
    assert verify(payload, "wrong-secret") is False


def test_verify_tampered_payload_fails():
    payload = {"signal_id": "x", "action": "BUY", "sl": 1.08}
    from src.utils.hmac_utils import sign, verify
    payload["hmac_sha256"] = sign(payload, "secret")
    payload["sl"] = 99999.0   # tamper
    assert verify(payload, "secret") is False
