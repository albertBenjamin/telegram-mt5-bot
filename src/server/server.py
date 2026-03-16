"""
E4 — FastAPI server: recibe señales del listener, las encola, las sirve al EA.

Restricciones:
- Solo acepta conexiones de 127.0.0.1 (middleware → 403 si no)
- HMAC-SHA256 obligatorio en cada señal (401 si falla)
- Rate limiting: 30 req/min por IP (slowapi → 429 si excede)
- Kill switch: bloquea nuevas señales y despacho al EA (503)
- DRY_RUN=True por defecto
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.store.dedup_store import DedupStore, Status
from src.utils.hmac_utils import verify as hmac_verify
from src.utils.logging_config import configure_logging
from src.utils.telegram_notify import send_alert

load_dotenv()

# ---------------------------------------------------------------------------
# Logging — stdout siempre; archivo JSON rotativo si LOG_FILE_SERVER está seteado
# ---------------------------------------------------------------------------
_LOG_FILE_SERVER = os.environ.get("LOG_FILE_SERVER", "")
configure_logging(Path(_LOG_FILE_SERVER) if _LOG_FILE_SERVER else None)
logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Config desde .env
# ---------------------------------------------------------------------------
HMAC_SECRET: str = os.environ.get("HMAC_SECRET", "")
DRY_RUN: bool = os.environ.get("DRY_RUN", "True").lower() == "true"
DB_PATH: Path = Path(os.environ.get("DB_PATH", "data/dedup.db"))

# ---------------------------------------------------------------------------
# Estado mutable del servidor
# (variables de módulo para que los tests puedan resetearlas)
# ---------------------------------------------------------------------------
_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
_kill_switch: bool = False
_dedup: DedupStore | None = None

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# Lifespan: inicializa y cierra el DedupStore
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _dedup

    # [P1] Doble confirmación para modo live — evita accidentes al copiar .env al VPS
    if not DRY_RUN:
        confirm = os.environ.get("CONFIRM_LIVE", "").lower()
        if confirm != "true":
            logger.error(
                "live_mode_not_confirmed",
                hint="DRY_RUN=False requiere CONFIRM_LIVE=true en .env para activar ejecucion real",
            )
            sys.exit(1)
        logger.warning(
            "live_mode_active",
            warning="DRY_RUN=False — Las ordenes se ejecutaran en la cuenta REAL",
        )

    _dedup = DedupStore(DB_PATH)
    logger.info("server_started", dry_run=DRY_RUN, db=str(DB_PATH))

    await send_alert(
        f"[BOT TRADING] Server arrancado\n"
        f"DRY_RUN: {DRY_RUN}\n"
        f"DB: {DB_PATH}"
    )

    yield
    _dedup.close()
    logger.info("server_stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# E4-2 — Middleware: rechaza cualquier IP que no sea 127.0.0.1
# ---------------------------------------------------------------------------
@app.middleware("http")
async def require_localhost(request: Request, call_next):
    host = request.client.host if request.client else ""
    if host != "127.0.0.1":
        return JSONResponse(
            status_code=403,
            content={"detail": "forbidden: only localhost connections allowed"},
        )
    response = await call_next(request)
    if response.status_code == 204:
        return Response(status_code=204)
    return response


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class EntrySchema(BaseModel):
    type: str
    price: float | None = None
    range_low: float | None = None
    range_high: float | None = None


class SignalPayload(BaseModel):
    signal_id: str
    timestamp: str
    raw_message: str
    source_channel: str
    action: str
    symbol: str
    entry: EntrySchema
    sl: float
    tps: list[float]
    hmac_sha256: str
    dry_run: bool


class ConfirmPayload(BaseModel):
    signal_id: str
    status: str            # "executed" | "failed"
    order_ticket: int | None = None


# ---------------------------------------------------------------------------
# E4-1 — Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/signal")
@limiter.limit("30/minute")
async def receive_signal(request: Request, payload: SignalPayload):
    """
    El listener envía aquí cada señal parseada y firmada.
    1. Kill switch → 503
    2. HMAC inválido → 401
    3. Duplicado → 409
    4. Encola → 200
    """
    if _kill_switch:
        raise HTTPException(status_code=503, detail="kill_switch_active")

    # E4-3 — Validar HMAC (si hay secret configurado)
    if HMAC_SECRET:
        if not hmac_verify(payload.model_dump(), HMAC_SECRET):
            logger.warning("invalid_hmac", signal_id=payload.signal_id)
            raise HTTPException(status_code=401, detail="invalid_hmac")

    # Dedup
    if not _dedup.mark_received(payload.signal_id):
        logger.info("duplicate_signal", signal_id=payload.signal_id)
        raise HTTPException(status_code=409, detail="duplicate_signal")

    # E4-5 — Cola asyncio (maxsize=100 — señales obsoletas se descartan con 503)
    try:
        _queue.put_nowait(payload.model_dump())
    except asyncio.QueueFull:
        logger.warning(
            "queue_full_signal_dropped",
            signal_id=payload.signal_id,
            maxsize=_queue.maxsize,
        )
        raise HTTPException(status_code=503, detail="queue_full")

    logger.info("signal_queued", signal_id=payload.signal_id, queue_size=_queue.qsize())

    tps_str = " | ".join(str(tp) for tp in payload.tps)
    await send_alert(
        f"[BOT TRADING] Nueva senal recibida\n"
        f"{payload.action} {payload.symbol}\n"
        f"SL: {payload.sl}\n"
        f"TPs: {tps_str}\n"
        f"DRY_RUN: {payload.dry_run}"
    )

    return {"signal_id": payload.signal_id, "queued": True}


@app.get("/api/v1/pending-signal")
@limiter.limit("120/minute")
async def get_pending_signal(request: Request):
    """
    El EA hace polling aquí cada 2s.
    - Kill switch activo → 503
    - Cola vacía → 204
    - Señal disponible → 200 con el JSON
    """
    if _kill_switch:
        raise HTTPException(status_code=503, detail="kill_switch_active")

    try:
        signal = _queue.get_nowait()
    except asyncio.QueueEmpty:
        return JSONResponse(status_code=204, content=None)

    _dedup.update_status(signal["signal_id"], Status.PENDING)
    logger.info("signal_dispatched", signal_id=signal["signal_id"])
    return signal


@app.post("/api/v1/confirm")
@limiter.limit("60/minute")
async def confirm_signal(request: Request, payload: ConfirmPayload):
    """
    El EA confirma la ejecución (o fallo) de una señal.
    """
    if payload.status not in (Status.EXECUTED, Status.FAILED):
        raise HTTPException(
            status_code=422,
            detail=f"invalid status: {payload.status!r}. Use 'executed' or 'failed'",
        )
    try:
        _dedup.update_status(payload.signal_id, payload.status)
    except KeyError:
        raise HTTPException(status_code=404, detail="signal_not_found")

    logger.info(
        "signal_confirmed",
        signal_id=payload.signal_id,
        status=payload.status,
        order_ticket=payload.order_ticket,
    )
    return {"signal_id": payload.signal_id, "status": payload.status}


@app.get("/health")
async def health(request: Request):
    return {
        "status": "ok",
        "queue_size": _queue.qsize(),
        "kill_switch": _kill_switch,
        "dry_run": DRY_RUN,
    }


# ---------------------------------------------------------------------------
# E4-6 — Kill switch
# ---------------------------------------------------------------------------

@app.post("/admin/kill-switch")
async def activate_kill_switch(request: Request):
    global _kill_switch
    _kill_switch = True
    logger.warning("kill_switch_activated")
    return {"kill_switch": True}


@app.post("/admin/resume")
async def resume(request: Request):
    global _kill_switch
    _kill_switch = False
    logger.info("kill_switch_deactivated")
    return {"kill_switch": False}
