# Deploy en VPS Windows — Guía paso a paso

## Requisitos del VPS

| Recurso | Mínimo recomendado |
|---------|-------------------|
| OS | Windows Server 2019 / Windows 10 Pro |
| RAM | 4 GB |
| Disco | 40 GB SSD |
| CPU | 2 vCPU |
| Red | IP fija o dominio (para acceso RDP) |

**Proveedores recomendados para Windows VPS:**
- Contabo (VPS M Windows) — económico, buena estabilidad
- Vultr (Cloud Compute, Windows)
- DigitalOcean no ofrece Windows — usar alternativa

> MetaTrader 5 requiere Windows. No es posible containerizar el EA en Linux.

---

## 1. Preparar el VPS

### 1.1 Conectar por RDP
```
mstsc /v:<ip-del-vps>
Usuario: Administrator (o el que provea el hosting)
```

### 1.2 Instalar Python 3.11+
1. Descargar desde https://www.python.org/downloads/windows/
2. Instalar con "Add Python to PATH" marcado
3. Verificar: `python --version`

### 1.3 Instalar MetaTrader 5
1. Descargar MT5 desde el broker (VT Markets: https://www.vantageforces.com/mt5)
2. Instalar y configurar la cuenta demo primero
3. Habilitar "Allow WebRequest for listed URL": `Herramientas > Opciones > Expert Advisors`
   - Añadir: `http://127.0.0.1:8080`

### 1.4 Instalar NSSM
1. Descargar desde https://nssm.cc/download
2. Extraer `nssm.exe` a `C:\Windows\System32\` (o a cualquier carpeta en PATH)
3. Verificar: `nssm version`

### 1.5 Instalar Git
1. Descargar desde https://git-scm.com/download/win
2. Instalar con opciones por defecto

---

## 2. Desplegar el código

### 2.1 Clonar el repositorio
```cmd
cd C:\
mkdir bot
cd bot
git clone <url-del-repo> telegram-mt5-bot
cd telegram-mt5-bot
```

### 2.2 Crear y activar el entorno virtual
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2.3 Verificar tests
```cmd
pytest tests/ -v
```
Los 91 tests deben pasar antes de continuar.

---

## 3. Configurar .env

```cmd
copy .env.example .env
notepad .env
```

Completar **todos** los campos:

```env
TELEGRAM_API_ID=<de https://my.telegram.org>
TELEGRAM_API_HASH=<de https://my.telegram.org>
TELEGRAM_SESSION=bot_session
WHITELIST_CHANNELS=<IDs numéricos de los canales>
HMAC_SECRET=<generar: python -c "import secrets; print(secrets.token_hex(32))">

# Para paper trading (demo):
DRY_RUN=True
CONFIRM_LIVE=          # dejar vacío en demo

# Logging (rutas absolutas recomendadas en producción):
LOG_FILE_SERVER=C:\bot\telegram-mt5-bot\logs\server.log
LOG_FILE_LISTENER=C:\bot\telegram-mt5-bot\logs\listener.log

# Alertas (opcional pero muy recomendado):
ALERT_BOT_TOKEN=<token de @BotFather>
ALERT_CHAT_ID=<tu chat_id de Telegram>
```

---

## 4. Autenticar Telethon (una sola vez)

La primera vez, Telethon necesita autenticación interactiva:

```cmd
cd C:\bot\telegram-mt5-bot
.venv\Scripts\activate
python src/listener/telegram_listener.py
```

Ingresar número de teléfono y código de verificación cuando se solicite.
El archivo `.session` se crea en la raíz del proyecto. **No lo borres.**

Detener con Ctrl+C después de que aparezca `listening_for_messages`.

---

## 5. Instalar los servicios Windows (NSSM)

### 5.1 Editar el script de instalación
Abrir `scripts\install_services.bat` y ajustar:
```bat
set PROJECT_DIR=C:\bot\telegram-mt5-bot   <- ajustar si difiere
set PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe
```

### 5.2 Ejecutar como Administrador
```cmd
:: Abrir CMD como Administrador
scripts\install_services.bat
```

Ambos servicios (`bot-server` y `bot-listener`) quedarán instalados y arrancados.

### 5.3 Verificar
```cmd
nssm status bot-server
nssm status bot-listener
```
Deben mostrar `SERVICE_RUNNING`.

---

## 6. Instalar el EA en MetaTrader 5

1. Copiar `ea\TelegramSignalEA.mq5` a `MQL5\Experts\` en MT5
   - Ruta típica: `C:\Users\<usuario>\AppData\Roaming\MetaQuotes\Terminal\<id>\MQL5\Experts\`
2. Compilar en MetaEditor (F7)
3. Abrir un gráfico de XAUUSD (o el símbolo que uses)
4. Arrastrar el EA al gráfico
5. En las propiedades del EA:
   - `ServerUrl`: `http://127.0.0.1:8080`
   - `HmacSecret`: el mismo valor que `HMAC_SECRET` en .env
   - `LotSize`: `0.01` para paper trading inicial
6. Habilitar "Allow live trading" y "Allow DLL imports"

---

## 7. Configurar health check (Task Scheduler)

1. Abrir `Programador de tareas` (Task Scheduler)
2. Crear tarea básica:
   - Nombre: `Bot Trading Health Check`
   - Disparador: cada 5 minutos, repetir indefinidamente
   - Acción: iniciar programa
     - Programa: `C:\bot\telegram-mt5-bot\.venv\Scripts\python.exe`
     - Argumentos: `scripts\health_check.py`
     - Directorio: `C:\bot\telegram-mt5-bot`
3. Guardar

---

## 8. Configurar backup diario (Task Scheduler)

1. Crear tarea básica:
   - Nombre: `Bot Trading Backup`
   - Disparador: diario a las 04:00
   - Acción: `C:\bot\telegram-mt5-bot\scripts\backup_db.bat`
   - Directorio: `C:\bot\telegram-mt5-bot`

---

## 9. Verificar pipeline completo

```cmd
:: Verificar que el server responde
curl http://127.0.0.1:8080/health

:: Ver logs en vivo
type logs\server.log
type logs\listener.log
```

Enviar una señal de prueba desde el canal Telegram whitelisted y verificar:
1. Listener loguea `signal_parsed`
2. Server loguea `signal_queued`
3. EA loguea `order_sent` (o `dry_run_skipped` si DRY_RUN=True)
4. Server loguea `signal_confirmed`

---

## 10. Pasar a cuenta real (checklist obligatorio)

- [ ] 2 semanas de paper trading en demo completadas sin errores
- [ ] Órdenes ejecutadas correctamente en demo (revisar historial MT5)
- [ ] Backup de `.session` y `dedup.db` realizado
- [ ] LOT_SIZE ajustado a 0.03 en propiedades del EA
- [ ] MT5 conectado a VTMarkets-Live7 con cuenta real
- [ ] En .env: `DRY_RUN=False` Y `CONFIRM_LIVE=true` (ambos requeridos)
- [ ] Server reiniciado y confirmado que muestra `live_mode_active` en logs
- [ ] Kill switch disponible: `curl -X POST http://127.0.0.1:8080/admin/kill-switch`

---

## Comandos útiles en producción

```cmd
:: Estado de servicios
nssm status bot-server
nssm status bot-listener

:: Reiniciar servicios
nssm restart bot-server
nssm restart bot-listener

:: Ver logs (últimas líneas)
powershell "Get-Content logs\server.log -Tail 50"
powershell "Get-Content logs\listener.log -Tail 50"

:: Health check manual
.venv\Scripts\python.exe scripts\health_check.py

:: Kill switch (emergencia)
curl -X POST http://127.0.0.1:8080/admin/kill-switch

:: Reanudar tras kill switch
curl -X POST http://127.0.0.1:8080/admin/resume

:: Backup manual
scripts\backup_db.bat
```
