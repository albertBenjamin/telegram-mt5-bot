# telegram-mt5-bot

Bot que lee señales de trading desde Telegram y las ejecuta automáticamente en MetaTrader 5.

## Arquitectura

```
Telegram → telegram_listener.py → FastAPI (127.0.0.1:8080) → TelegramSignalEA.mq5 → MT5
```

Tres procesos independientes comunicados via HTTP + HMAC-SHA256:

| Proceso | Tecnología | Función |
|---------|-----------|---------|
| Listener | Python / Telethon | Escucha canales Telegram, parsea señales, firma con HMAC y envía al server |
| Server | Python / FastAPI | Recibe señales, dedup SQLite, cola asyncio, expone endpoints al EA |
| EA | MQL5 / MetaTrader 5 | Polling cada 2s, valida HMAC, ejecuta órdenes, confirma ejecución |

## Estado

**En producción.** Cuenta real VTMarkets-Live7 (#24430609), símbolo XAUUSD-STD.

## Requisitos

- Python 3.11+
- MetaTrader 5 (Windows)
- Credenciales Telegram API (https://my.telegram.org)

## Setup rápido

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # completar con credenciales reales
pytest tests/ -v
```

## Deploy en VPS

Ver `deploy/README_deploy.md` para la guía completa paso a paso.

## Documentación interna

- `CLAUDE.md` — arquitectura, decisiones técnicas, convenciones de código
- `BACKLOG.md` — estado del proyecto, historial de cambios, pendientes
- `deploy/README_deploy.md` — guía de deploy en VPS Windows
