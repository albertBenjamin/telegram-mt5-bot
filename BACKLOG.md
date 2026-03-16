# Bot Trading — Backlog

## Estado general
- Código: production-ready (Fase 2 completa)
- Tests: 93 pasando
- Pendiente: deploy en VPS Windows

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

---

## Pendiente — Lunes: Deploy en VPS

### 0. Contratar VPS Windows
Ver sección "Opciones VPS" abajo. Recomendación: preguntar primero a VT Markets.

### 1. Seguir deploy/README_deploy.md
Cubre: Python, MT5, NSSM, clonar repo, venv, tests, autenticación Telethon.

### 2. Configurar .env en el VPS
```env
DRY_RUN=False
CONFIRM_LIVE=true
ALERT_BOT_TOKEN=<token>
ALERT_CHAT_ID=<chat_id>
LOG_FILE_SERVER=logs/server.log
LOG_FILE_LISTENER=logs/listener.log
```

### 3. Configurar MT5
- Cuenta: #24430609
- Servidor: VTMarkets-Live7
- Símbolo: XAUUSD-STD
- LotSize en propiedades del EA: 0.03
- Habilitar WebRequest para: http://127.0.0.1:8080

### 4. Verificar antes de dejar correr
- [ ] `nssm status bot-server` → RUNNING
- [ ] `nssm status bot-listener` → RUNNING
- [ ] `curl http://127.0.0.1:8080/health` → `{"status":"ok"}`
- [ ] Health check manual llega al bot: `python scripts/health_check.py`
- [ ] Alerta de arranque llegó al Telegram
- [ ] Enviar señal de prueba desde el canal y verificar que el EA la ejecuta

---

## Fase 3 — E7 Hardening (post-deploy, cuando el bot esté estable)
- [ ] Monitoreo de equity/drawdown (pausar si pérdida supera X%)
- [ ] Reconexión automática del listener si se cae Telethon
- [ ] Tests de integración end-to-end automatizados
- [ ] Rotación del HMAC_SECRET sin downtime

---

## Opciones VPS Windows

| Opción | Precio | Notas |
|--------|--------|-------|
| **VT Markets VPS** | Gratis/subsidiado | Revisar en tu portal — brokers suelen dar VPS gratis con volumen. Primera opción. |
| **Contabo** | ~$14/mes | 4 vCPU, 8GB RAM, Windows. El más barato que funciona bien. |
| **Hostinger VPS** | ~$12-18/mes | Fácil de configurar, buena latencia. |
| AWS / Azure / GCP | ~$30-50/mes | Más confiable pero overkill para este caso. |

**Specs mínimas necesarias:** 2 vCPU, 4 GB RAM, Windows Server 2019.
MT5 + Python + FastAPI son ligeros — Contabo es suficiente.

**Recomendación:** primero consulta a VT Markets si tienen VPS gratis (algunos brokers lo dan con cierto volumen). Si no, ve con Contabo.
