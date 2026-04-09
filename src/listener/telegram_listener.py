import atexit
import asyncio
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Asegurar que el proyecto raíz esté en sys.path al correr como script directo
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import structlog
from dotenv import load_dotenv
from telethon import TelegramClient, events

from src.parser.models import NoOpSignal, ParseError, ValidationError
from src.parser.signal_parser import process
from src.utils.hmac_utils import sign
from src.utils.logging_config import configure_logging

load_dotenv()

# --- Logging — stdout siempre; archivo JSON rotativo si LOG_FILE_LISTENER está seteado ---
_LOG_FILE_LISTENER = os.environ.get("LOG_FILE_LISTENER", "")
configure_logging(Path(_LOG_FILE_LISTENER) if _LOG_FILE_LISTENER else None)

logger = structlog.get_logger()

# --- Rutas ---
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PID_FILE = PROJECT_ROOT / "listener.pid"

# --- Ventana de mensajes viejos ---
STALE_MESSAGE_WINDOW = timedelta(seconds=60)

# Hora de conexión — se establece en main() tras conectar
_connected_at: datetime | None = None


# ---------------------------------------------------------------------------
# E1-4 — Lock de instancia única (PID file)
# ---------------------------------------------------------------------------

def _acquire_pid_lock() -> None:
    if PID_FILE.exists():
        try:
            existing_pid = int(PID_FILE.read_text().strip())
        except ValueError:
            PID_FILE.unlink(missing_ok=True)
        else:
            # Comprobar si el proceso sigue vivo (Windows + Unix)
            try:
                os.kill(existing_pid, 0)
                logger.error(
                    "instance_already_running",
                    pid=existing_pid,
                    pid_file=str(PID_FILE),
                )
                sys.exit(1)
            except (OSError, ProcessLookupError):
                logger.warning("stale_pid_file_removed", pid=existing_pid)
                PID_FILE.unlink(missing_ok=True)

    PID_FILE.write_text(str(os.getpid()))
    atexit.register(_release_pid_lock)
    logger.info("pid_lock_acquired", pid=os.getpid(), pid_file=str(PID_FILE))


def _release_pid_lock() -> None:
    PID_FILE.unlink(missing_ok=True)
    logger.info("pid_lock_released")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        logger.error("missing_env_var", key=key)
        sys.exit(1)
    return value


def _parse_whitelist() -> frozenset[int]:
    raw = _get_env("WHITELIST_CHANNELS")
    try:
        return frozenset(int(cid.strip()) for cid in raw.split(",") if cid.strip())
    except ValueError:
        logger.error("invalid_whitelist_format", raw=raw)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_ID   = int(_get_env("TELEGRAM_API_ID"))
API_HASH = _get_env("TELEGRAM_API_HASH")
SESSION  = os.environ.get("TELEGRAM_SESSION", "bot_session")
WHITELIST_CHANNELS = _parse_whitelist()

HMAC_SECRET = os.environ.get("HMAC_SECRET", "")
DRY_RUN     = os.environ.get("DRY_RUN", "True").lower() == "true"
API_HOST    = os.environ.get("API_HOST", "127.0.0.1")
API_PORT    = os.environ.get("API_PORT", "8080")
SERVER_URL  = f"http://{API_HOST}:{API_PORT}/api/v1/signal"

_whitelist_symbols_raw = os.environ.get("WHITELIST_SYMBOLS", "")
WHITELIST_SYMBOLS: frozenset[str] | None = (
    frozenset(s.strip() for s in _whitelist_symbols_raw.split(",") if s.strip())
    if _whitelist_symbols_raw else None
)

client = TelegramClient(SESSION, API_ID, API_HASH)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def _send_signal(payload: dict) -> None:
    """POST la señal firmada al server FastAPI. Fire-and-forget con log de errores."""
    try:
        async with httpx.AsyncClient() as client_http:
            resp = await client_http.post(SERVER_URL, json=payload, timeout=5.0)
        if resp.status_code == 200:
            logger.info("signal_sent", signal_id=payload["signal_id"])
        elif resp.status_code == 409:
            logger.info("signal_duplicate", signal_id=payload["signal_id"])
        else:
            logger.warning("signal_send_failed", status=resp.status_code, body=resp.text)
    except httpx.RequestError as exc:
        logger.error("signal_send_error", error=str(exc), signal_id=payload["signal_id"])


@client.on(events.NewMessage)
async def handle_new_message(event: events.NewMessage.Event) -> None:
    # E1-2 — Whitelist
    if event.chat_id not in WHITELIST_CHANNELS:
        logger.debug("message_dropped_not_whitelisted", channel_id=event.chat_id)
        return

    # E1-3 — Ignorar mensajes viejos al reconectar
    if _connected_at is not None:
        age = _connected_at - event.date.replace(tzinfo=timezone.utc)
        if age > STALE_MESSAGE_WINDOW:
            logger.info(
                "message_dropped_stale",
                channel_id=event.chat_id,
                message_id=event.id,
                age_seconds=int(age.total_seconds()),
            )
            return

    safe_text = event.raw_text.encode('utf-8', errors='replace').decode('utf-8')
    logger.info(
        "raw_message_received",
        channel_id=event.chat_id,
        message_id=event.id,
        date=event.date.isoformat(),
        text=safe_text,
    )

    # E2 — Parsear
    try:
        result = process(
            text=event.raw_text,
            channel_id=event.chat_id,
            message_id=event.id,
            allowed_symbols=WHITELIST_SYMBOLS,
        )
    except ParseError as exc:
        logger.warning("parse_error", error=str(exc), message_id=event.id)
        return
    except ValidationError as exc:
        logger.warning("validation_error", error=str(exc), message_id=event.id)
        return

    if isinstance(result, NoOpSignal):
        logger.info("noop_signal", reason=result.reason, message_id=event.id)
        return

    # Construir payload con todos los campos que espera SignalPayload
    # (incluyendo None explícitos para que el HMAC canónico coincida con el server)
    timestamp = event.date.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict = {
        "signal_id":      result.signal_id,
        "timestamp":      timestamp,
        "raw_message":    result.raw_message,
        "source_channel": result.source_channel,
        "action":         result.action.value,
        "symbol":         result.symbol,
        "entry": {
            "type":        result.entry.type.value,
            "price":       result.entry.price,
            "range_low":   result.entry.range_low,
            "range_high":  result.entry.range_high,
        },
        "sl":       result.sl,
        "tps":      result.tps,
        "hmac_sha256": "",
        "dry_run":  DRY_RUN,
    }

    # Firmar (sign() excluye hmac_sha256 del canónico internamente)
    payload["hmac_sha256"] = sign(payload, HMAC_SECRET) if HMAC_SECRET else ""

    logger.info(
        "signal_parsed",
        signal_id=result.signal_id,
        action=result.action.value,
        symbol=result.symbol,
        dry_run=DRY_RUN,
    )

    await _send_signal(payload)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    _acquire_pid_lock()

    logger.info("listener_starting", session=SESSION)
    await client.start()

    global _connected_at
    _connected_at = datetime.now(timezone.utc)

    me = await client.get_me()
    logger.info("listener_connected", username=me.username, user_id=me.id)
    logger.info("whitelist_loaded", channels=sorted(WHITELIST_CHANNELS))
    logger.info(
        "stale_window_set",
        window_seconds=int(STALE_MESSAGE_WINDOW.total_seconds()),
        connected_at=_connected_at.isoformat(),
    )
    logger.info("listening_for_messages")

    await client.run_until_disconnected()


if __name__ == "__main__":
    # [P2] SIGTERM graceful shutdown — NSSM envía SIGTERM al detener el servicio.
    # sys.exit(0) dispara atexit → _release_pid_lock limpia el PID file.
    def _handle_sigterm(signum, frame) -> None:
        logger.info("shutdown_signal_received", signal=signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass  # atexit ya libera el PID lock
