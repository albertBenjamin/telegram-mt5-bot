#!/usr/bin/env python3
"""
Health check para el server FastAPI.

Diseñado para correr cada 5 minutos via Windows Task Scheduler.
Si el server falla 2 veces consecutivas, envía alerta a Telegram.

Estado de fallos: scripts/.hc_state.json (archivo local, se crea automáticamente)

Uso:
    python scripts/health_check.py

Configurar en Task Scheduler:
    Programa  : C:\\...\\telegram-mt5-bot\\.venv\\Scripts\\python.exe
    Argumentos: scripts\\health_check.py
    Directorio: C:\\...\\telegram-mt5-bot
    Disparador: cada 5 minutos, indefinidamente
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Cargar .env desde la raíz del proyecto (un nivel arriba de scripts/)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # dotenv no disponible: leer env vars del sistema

try:
    import httpx
except ImportError:
    print("[ERROR] httpx no instalado. Activa el venv antes de correr este script.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("ALERT_BOT_TOKEN", "")
CHAT_ID    = os.environ.get("ALERT_CHAT_ID", "")
API_PORT   = os.environ.get("API_PORT", "8080")

HEALTH_URL       = f"http://127.0.0.1:{API_PORT}/health"
STATE_FILE       = SCRIPT_DIR / ".hc_state.json"
FAILURE_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Estado persistente (fallos consecutivos)
# ---------------------------------------------------------------------------

def _read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"consecutive_failures": 0}


def _write_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# Alerta Telegram
# ---------------------------------------------------------------------------

def _send_telegram(text: str) -> None:
    if not (BOT_TOKEN and CHAT_ID):
        print("[WARN] ALERT_BOT_TOKEN o ALERT_CHAT_ID no configurados — alerta no enviada")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json={"chat_id": CHAT_ID, "text": text})
        if resp.status_code != 200:
            print(f"[WARN] Telegram API error: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"[ERROR] No se pudo enviar alerta Telegram: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = _read_state()

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(HEALTH_URL)

        if resp.status_code == 200:
            data = resp.json()
            print(
                f"[{ts}] OK — "
                f"queue={data.get('queue_size', '?')}  "
                f"kill_switch={data.get('kill_switch', '?')}  "
                f"dry_run={data.get('dry_run', '?')}"
            )
            state["consecutive_failures"] = 0
            _write_state(state)
            return

        error_msg = f"HTTP {resp.status_code}: {resp.text[:100]}"

    except Exception as exc:
        error_msg = str(exc)

    # --- Fallo ---
    state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
    print(f"[{ts}] FALLO #{state['consecutive_failures']}: {error_msg}")
    _write_state(state)

    if state["consecutive_failures"] >= FAILURE_THRESHOLD:
        alert = (
            f"[BOT TRADING] ALERTA: Server caido\n"
            f"Timestamp: {ts}\n"
            f"Error: {error_msg}\n"
            f"Fallos consecutivos: {state['consecutive_failures']}\n"
            f"URL: {HEALTH_URL}"
        )
        _send_telegram(alert)
        print(f"[{ts}] Alerta enviada a Telegram")


if __name__ == "__main__":
    main()
