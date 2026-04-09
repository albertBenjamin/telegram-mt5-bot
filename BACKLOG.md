# Bot Trading — Backlog

## Estado general
- **BOT EN PRODUCCIÓN** — cuenta real #24430609, VTMarkets-Live7, XAUUSD-STD
- Tests: 93 pasando
- Pipeline validado: señal test → 3 SELL_LIMIT ejecutadas (tickets 1115644, 1115645, 1115647)

---

## Completado

### Fase 1 — Core (E0–E6)
- [x] E0 Entorno, estructura, venv
- [x] E1 Listener Telethon (whitelist, PID lock, stale 60s)
- [x] E2 Parser (regex anti-ReDoS, validación, NoOp, 48 tests)
- [x] E3 DedupStore SQLite (WAL, idempotencia, concurrencia, 19 tests)
- [x] E4 FastAPI server (6 endpoints, HMAC, rate limit, kill switch, 26 tests)
- [x] E5 EA MQL5 (polling 2s, HMAC, OrderSend, 3 órdenes por señal, midpoint RANGE)
- [x] E6 Integración end-to-end verificada en dry-run

### Fase 2 — Hardening pre-VPS
- [x] Doble confirmación DRY_RUN (requiere CONFIRM_LIVE=true para modo real)
- [x] Logging a archivo con rotación (RotatingFileHandler 5MB×3, JSON)
- [x] asyncio.Queue(maxsize=100) con 503 si se llena
- [x] SIGTERM graceful en el listener (libera PID lock limpiamente)
- [x] Health check con alerta Telegram (2 fallos consecutivos → notificación)
- [x] Alerta Telegram al arrancar server y por cada señal recibida
- [x] NSSM install_services.bat (auto-restart, Windows Services)
- [x] backup_db.bat (dedup.db + .session diario, retención 7 días)
- [x] deploy/README_deploy.md (guía completa de 0 a producción)

### Deploy VPS y puesta en producción
- [x] VPS Windows contratado y configurado
- [x] Python, MT5, NSSM instalados (`C:\tools\nssm.exe` — no en PATH)
- [x] Servicios NSSM registrados: `bot-server` y `bot-listener`
- [x] MT5 conectado a VTMarkets-Live7, cuenta #24430609, símbolo XAUUSD-STD
- [x] `.env` configurado con DRY_RUN=False + CONFIRM_LIVE=true
- [x] Token bot Telegram regenerado desde BotFather (token anterior dio 401 Unauthorized)
- [x] Health check verificado: alerta llega al bot antes de dejar correr

### Fixes EA — VT Markets Live
- [x] **Fix símbolo**: EA usaba `sig.symbol` ("XAUUSD") → cambiado a `Symbol()` (símbolo del gráfico = "XAUUSD-STD"). HMAC sigue validándose con `sig.symbol` del payload (correcto).
- [x] **Fix Market Execution**: `req.type_filling` para órdenes pendientes (RANGE/LIMIT) forzado a `ORDER_FILLING_RETURN`. Órdenes de mercado (DEAL) siguen usando `InpFilling` configurable.

---

## Pendiente — E7 Hardening (cuando el bot esté estable en producción)

- [ ] Monitoreo de equity/drawdown (pausar si pérdida supera X%)
- [ ] Reconexión automática del listener si se cae Telethon
- [ ] Tests de integración end-to-end automatizados
- [ ] Rotación del HMAC_SECRET sin downtime

---

## Referencia VPS

| Item | Valor |
|------|-------|
| NSSM | `C:\tools\nssm.exe` (no en PATH) |
| Cuenta MT5 | #24430609 |
| Servidor MT5 | VTMarkets-Live7 |
| Símbolo | XAUUSD-STD |
| LotSize | 0.03 |
| DRY_RUN | False |
| CONFIRM_LIVE | true |

**Comandos útiles en el VPS:**
```cmd
C:\tools\nssm.exe status bot-server
C:\tools\nssm.exe status bot-listener
C:\tools\nssm.exe restart bot-server
curl -X POST http://127.0.0.1:8080/admin/kill-switch
python scripts/health_check.py
```
