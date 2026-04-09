# CLAUDE.md — telegram-mt5-bot

Bot que lee señales de trading desde Telegram y las ejecuta automáticamente en MetaTrader 5.

## Arquitectura

```
Telegram → telegram_listener.py → FastAPI server (127.0.0.1:8080) → TelegramSignalEA.mq5 → MT5
```

Tres procesos independientes que se comunican via HTTP con HMAC-SHA256:
1. **Listener** (Telethon/MTProto): escucha canales whitelisted, parsea, firma y envía al server
2. **Server** (FastAPI): recibe señales, dedup, cola asyncio, expone endpoints al EA
3. **EA** (MQL5): polling cada 2s, valida HMAC, ejecuta órdenes, confirma ejecución

## Stack

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.11+ |
| Telegram client | Telethon (MTProto, no Bot API) |
| HTTP server | FastAPI + Uvicorn |
| Base de datos | SQLite (dedup store, MVP) |
| EA | MQL5 en MetaTrader 5 |
| Tests | pytest |
| Logs | structlog (JSON) |
| Rate limiting | slowapi |

## Estructura de carpetas

```
telegram-mt5-bot/
  src/
    listener/
      telegram_listener.py
    parser/
      signal_parser.py
      models.py
    server/
      server.py
    store/
      dedup_store.py
    utils/
      hmac_utils.py
      logging_config.py   ← stdout + RotatingFileHandler JSON
      telegram_notify.py  ← alertas Telegram Bot API
  ea/
    TelegramSignalEA.mq5
  tests/
    test_parser.py
    test_dedup.py
    test_server.py
  scripts/
    health_check.py        ← health check + alerta Telegram (Task Scheduler)
    install_services.bat   ← registra servicios NSSM
    backup_db.bat          ← backup diario dedup.db + .session
  deploy/
    README_deploy.md       ← guía completa VPS Windows
  data/
    dedup.db               # generado en runtime, en .gitignore
  logs/                    # generado en runtime, en .gitignore
  .env                     # NUNCA commitear
  .env.example             # plantilla sin valores reales
  requirements.txt
  BACKLOG.md
  CLAUDE.md
```

## Reglas de seguridad (no negociables)

- `.env` y `*.session` siempre en `.gitignore` — verificar antes de cada commit
- El servidor FastAPI solo escucha en `127.0.0.1`, nunca `0.0.0.0`
- `DRY_RUN=True` por defecto — cambiar a `False` requiere acción explícita del usuario
- **Fail-closed**: cualquier ambigüedad en el parser → `ParseError` → no ejecutar
- HMAC-SHA256 obligatorio en cada señal — el EA rechaza sin HMAC válido
- Nunca pasar a cuenta real sin 2 semanas de paper trading en demo

## Variables de entorno (.env)

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION=bot_session
WHITELIST_CHANNELS=        # IDs numéricos separados por coma
HMAC_SECRET=               # generar con: python -c "import secrets; print(secrets.token_hex(32))"
DRY_RUN=True
CONFIRM_LIVE=              # requerido = "true" cuando DRY_RUN=False (doble confirmación)
API_HOST=127.0.0.1
API_PORT=8080
WHITELIST_SYMBOLS=XAUUSD,EURUSD,GBPUSD,USDJPY,GBPJPY
LOG_FILE_SERVER=logs/server.log      # vacío = solo stdout
LOG_FILE_LISTENER=logs/listener.log  # vacío = solo stdout
ALERT_BOT_TOKEN=           # token bot de alertas (BotFather) — regenerar si da 401
ALERT_CHAT_ID=             # chat_id destino de alertas
```

## Modelo de señal (JSON)

```json
{
  "signal_id": "sha256(channel_id:message_id)",
  "timestamp": "2025-03-04T14:30:00Z",
  "raw_message": "SELL XAUUSD 5181-5185 / SL 5189 / TP 5179 5177 5174",
  "source_channel": "-1001234567890",
  "action": "BUY | SELL",
  "symbol": "XAUUSD",
  "entry": {
    "type": "MARKET | RANGE | LIMIT",
    "range_low": 5181.0,
    "range_high": 5185.0
  },
  "sl": 5189.0,
  "tps": [5179.0, 5177.0, 5174.0],
  "hmac_sha256": "...",
  "dry_run": true
}
```

## Formatos de señal soportados

```
SELL XAUUSD 5181-5185 / SL 5189 / TP 5179 5177 5174   ← rango de entrada
BUY EURUSD / SL 1.0800 / TP 1.0900                    ← market (sin precio)
SELL GBPUSD 1.2650 / SL 1.2680 / TP 1.2620            ← precio exacto (limit)
```

Separadores válidos: `/` o `|`. El parser usa `re.fullmatch()`, nunca `re.match()`.

Mensajes de actualización (`move SL`, `TP1 hit`, `close`, `cancelled`) → `NoOpSignal` (ignorar silenciosamente, no es error).

## Validación lógica de precios (obligatoria)

- **SELL**: `SL > precio_entrada > TP[0]`
- **BUY**: `SL < precio_entrada < TP[0]`
- Si no se cumple → `ValidationError` → no ejecutar (fail-closed)

Para rangos de entrada, usar `range_high` como precio de referencia en SELL y `range_low` en BUY.

## API endpoints (FastAPI)

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/signal` | Recibe señal del listener |
| GET | `/api/v1/pending-signal` | EA consulta señal pendiente |
| POST | `/api/v1/confirm` | EA confirma ejecución |
| GET | `/health` | Health check |
| POST | `/admin/kill-switch` | Pausar ejecución |
| POST | `/admin/resume` | Reanudar ejecución |

Restricción: middleware 403 si la request no viene de `127.0.0.1`.

## Anti-patrones a evitar

- Nunca `re.match()` en el parser — siempre `re.fullmatch()`
- Nunca hardcodear credenciales — siempre `.env`
- Nunca `host='0.0.0.0'` en el server
- Nunca ejecutar señal si hay ambigüedad — fail-closed siempre
- Nunca pasar a cuenta real sin paper trading completo en demo

## Convenciones de código

- Errores del parser: `ParseError` (ambigüedad o formato inválido), `ValidationError` (precios incoherentes), `NoOpSignal` (mensaje de actualización)
- `signal_id = SHA256(f"{channel_id}:{message_id}")`
- Timeout anti-ReDoS en el parser: ≤ 500ms
- Tests parametrizados con pytest: mínimo 30 casos para el parser (casos felices, errores esperados, NO-OP)
- Rate limiting: 30 req/min con slowapi

## Secuencia de desarrollo (respetar orden)

```
E0 (entorno) → E1 (listener) → E2 (parser) → E3 (dedup) → E4 (server) → E5 (EA) → E6 (integración) → E7 (hardening)
```

No avanzar al siguiente epic sin tests pasando en el actual.

## Comandos frecuentes

```bash
# Entorno virtual
source .venv/bin/activate          # Linux/Mac
.venv\Scripts\activate             # Windows

# Correr el server (desarrollo)
uvicorn src.server.server:app --host 127.0.0.1 --port 8080 --reload

# Correr el server (producción — sin reload)
uvicorn src.server.server:app --host 127.0.0.1 --port 8080 --workers 1

# Correr el listener
python src/listener/telegram_listener.py

# Tests
pytest tests/ -v

# Generar HMAC_SECRET
python -c "import secrets; print(secrets.token_hex(32))"

# Health check manual
python scripts/health_check.py

# Kill switch de emergencia (desde el VPS)
curl -X POST http://127.0.0.1:8080/admin/kill-switch

# Estado servicios NSSM (VPS — nssm no está en PATH)
C:\tools\nssm.exe status bot-server
C:\tools\nssm.exe status bot-listener
```

## Estado actual del proyecto

**BOT EN PRODUCCIÓN. 93 tests pasando.**

### Estado por epic

| Epic | Estado | Tests |
|---|---|---|
| E0 — Entorno | ✅ | — |
| E1 — Telegram Listener | ✅ | — |
| E2 — Signal Parser | ✅ | 48 |
| E3 — Dedup Store | ✅ | 19 |
| E4 — API Server | ✅ | 26 |
| E5 — EA MQL5 | ✅ | — |
| E6 — Integración | ✅ | — |
| Fase 2 — Hardening pre-VPS | ✅ | — |
| E7 — Hardening post-deploy | ⏳ pendiente | — |

### Configuración de producción (VPS)

- **NSSM**: `C:\tools\nssm.exe` — no está en PATH, invocar con ruta completa
- **Servicios**: `bot-server` y `bot-listener` (auto-start, auto-restart con NSSM)
- **Cuenta MT5**: #24430609, servidor VTMarkets-Live7
- **Símbolo**: XAUUSD-STD en el gráfico del EA; el canal envía "XAUUSD" (el EA mapea con `Symbol()`)
- **LotSize EA**: 0.03
- **DRY_RUN**: False en producción (requiere `CONFIRM_LIVE=true`)
- **Alerta bot Telegram**: regenerar token desde BotFather si aparece error 401 Unauthorized

### Validación en producción

- Pipeline end-to-end validado en cuenta real #24430609 (VTMarkets-Live7)
- Señal test SELL XAUUSD → 3 `SELL_LIMIT` ejecutadas (tickets 1115644, 1115645, 1115647)
- `status=executed` confirmado por EA al server

### Archivos clave implementados

```
src/listener/telegram_listener.py  — Telethon, whitelist, SIGTERM graceful, logging a archivo
src/listener/list_channels.py      — helper de un solo uso para obtener channel IDs
src/parser/models.py               — ParsedSignal, EntryPrice, ParseError, ValidationError, NoOpSignal
src/parser/signal_parser.py        — process(), parse(), validate(); regex anti-ReDoS
src/store/dedup_store.py           — DedupStore SQLite, WAL, threading.Lock
src/server/server.py               — FastAPI, 6 endpoints, Queue(maxsize=100), DRY_RUN doble confirmación
src/utils/hmac_utils.py            — sign(), verify() con canonical JSON + compare_digest
src/utils/logging_config.py        — configure_logging(): stdout ConsoleRenderer + RotatingFileHandler JSON
src/utils/telegram_notify.py       — send_alert() async: alertas startup + señal recibida
scripts/health_check.py            — polling /health cada 5 min, alerta tras 2 fallos (Task Scheduler)
scripts/install_services.bat       — registra bot-server y bot-listener con NSSM
scripts/backup_db.bat              — backup diario dedup.db + .session, retención 7 días
deploy/README_deploy.md            — guía completa de 0 a producción en VPS Windows
ea/TelegramSignalEA.mq5            — polling 2s, Symbol() para ejecución, HMAC con payload symbol
tests/test_parser.py               — 48 tests parametrizados
tests/test_dedup.py                — 19 tests (idempotencia, persistencia, concurrencia)
tests/test_server.py               — 26 tests con httpx + anyio
```

### Decisiones técnicas importantes ya tomadas

- **Regex anti-ReDoS**: `_NUM = r'\d{1,10}(?:\.\d{1,8})?'` — cuantificadores acotados, sin anidamiento
- **Precio de referencia en RANGE**: `range_high` para SELL, `range_low` para BUY
- **HMAC canonical**: `json.dumps(payload_sin_hmac, sort_keys=True, separators=(',',':'))`
  - El payload debe incluir todos los campos Pydantic (con `None`) al firmar, porque `model_dump()` los incluye
- **DedupStore**: `INSERT OR IGNORE` atómico + `PRAGMA journal_mode=WAL`
- **Estado del server**: variables de módulo (`_queue`, `_kill_switch`, `_dedup`) reseteables en tests
- **Tests async**: `@pytest.mark.anyio` + `httpx.AsyncClient` con `ASGITransport`
- **IP externa en tests**: `ASGITransport(app=app, client=("10.0.0.1", 9999))`
- **EA — símbolo**: `Symbol()` del gráfico para ejecución; `sig.symbol` del payload solo para HMAC canonical
- **EA — Market Execution filling**: `ORDER_FILLING_RETURN` forzado en `TRADE_ACTION_PENDING`; `InpFilling` configurable solo en `TRADE_ACTION_DEAL`
- **Logging**: processor tee en structlog — escribe JSON a archivo antes de ConsoleRenderer; no requiere stdlib integration

### E1 — detalles del listener

- Canal whitelisted: `-1003224347994` (GOLD VIP 2.0) en `WHITELIST_CHANNELS`
- PID file: `listener.pid` en la raíz del proyecto (en `.gitignore`)
- Ventana stale messages: 60 segundos tras reconexión

### E4 — comportamiento de endpoints

- `POST /api/v1/signal`: kill_switch→503, HMAC→401, duplicate→409, queue_full→503, ok→200
- `GET /api/v1/pending-signal`: kill_switch→503, vacío→204, señal→200
- `POST /api/v1/confirm`: status válido: `"executed"` | `"failed"`, unknown→404, bad_status→422
- Kill switch bloquea tanto recepción como despacho de señales
