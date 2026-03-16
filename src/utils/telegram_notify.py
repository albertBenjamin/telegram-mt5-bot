"""
Envía alertas a Telegram via Bot API (httpx).

Requiere en .env:
    ALERT_BOT_TOKEN=<token del bot de alertas>
    ALERT_CHAT_ID=<tu chat_id o group_id>

Si alguna variable falta, las funciones retornan silenciosamente sin lanzar.
"""
import os

import httpx
import structlog

logger = structlog.get_logger()

_TELEGRAM_API = "https://api.telegram.org"


async def send_alert(text: str) -> None:
    """Envía alerta a Telegram (async). Fire-and-forget — no lanza excepciones."""
    bot_token = os.environ.get("ALERT_BOT_TOKEN", "")
    chat_id = os.environ.get("ALERT_CHAT_ID", "")
    if not (bot_token and chat_id):
        return
    url = f"{_TELEGRAM_API}/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
        if resp.status_code != 200:
            logger.warning(
                "telegram_alert_failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
    except Exception as exc:
        logger.error("telegram_alert_error", error=str(exc))
