@echo off
setlocal EnableDelayedExpansion

:: ============================================================================
:: install_services.bat
:: Registra bot-server y bot-listener como Windows Services via NSSM.
::
:: Requisitos:
::   1. NSSM descargado en %NSSM_EXE% (ver abajo) o en el PATH
::      Descarga: https://nssm.cc/download
::   2. Ejecutar como Administrador
::   3. Ajustar PROJECT_DIR y PYTHON_EXE si difieren
::
:: Uso (en cmd como Administrador):
::   scripts\install_services.bat
:: ============================================================================

:: --------------------------------------------------------------------------
:: CONFIGURACION — ajustar estas rutas al VPS
:: --------------------------------------------------------------------------
set PROJECT_DIR=C:\bot\telegram-mt5-bot
set PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe
set NSSM_EXE=nssm
:: Si nssm no está en PATH, usar ruta completa: set NSSM_EXE=C:\tools\nssm\nssm.exe

set LOG_DIR=%PROJECT_DIR%\logs
set SERVICE_SERVER=bot-server
set SERVICE_LISTENER=bot-listener

:: --------------------------------------------------------------------------
:: Validaciones
:: --------------------------------------------------------------------------
if not exist "%PROJECT_DIR%" (
    echo [ERROR] PROJECT_DIR no existe: %PROJECT_DIR%
    echo Edita este script y ajusta PROJECT_DIR.
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python del venv no encontrado: %PYTHON_EXE%
    echo Activa el venv primero: cd %PROJECT_DIR% ^&^& python -m venv .venv
    pause
    exit /b 1
)

where %NSSM_EXE% >nul 2>&1
if errorlevel 1 (
    echo [ERROR] nssm no encontrado en PATH.
    echo Descarga desde https://nssm.cc/download y ponlo en PATH o ajusta NSSM_EXE.
    pause
    exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: --------------------------------------------------------------------------
:: Desinstalar servicios previos si existen (evita errores en re-instalacion)
:: --------------------------------------------------------------------------
echo.
echo Eliminando servicios previos (si existen)...
%NSSM_EXE% stop %SERVICE_SERVER% confirm >nul 2>&1
%NSSM_EXE% remove %SERVICE_SERVER% confirm >nul 2>&1
%NSSM_EXE% stop %SERVICE_LISTENER% confirm >nul 2>&1
%NSSM_EXE% remove %SERVICE_LISTENER% confirm >nul 2>&1

:: --------------------------------------------------------------------------
:: bot-server — FastAPI + uvicorn
:: --------------------------------------------------------------------------
echo.
echo Instalando servicio: %SERVICE_SERVER%...

%NSSM_EXE% install %SERVICE_SERVER% "%PYTHON_EXE%"
%NSSM_EXE% set %SERVICE_SERVER% AppParameters -m uvicorn src.server.server:app --host 127.0.0.1 --port 8080 --workers 1
%NSSM_EXE% set %SERVICE_SERVER% AppDirectory "%PROJECT_DIR%"
%NSSM_EXE% set %SERVICE_SERVER% AppEnvironmentExtra "PYTHONPATH=%PROJECT_DIR%"
%NSSM_EXE% set %SERVICE_SERVER% DisplayName "Bot Trading — FastAPI Server"
%NSSM_EXE% set %SERVICE_SERVER% Description "FastAPI server que recibe senales del listener y las sirve al EA MT5"
%NSSM_EXE% set %SERVICE_SERVER% Start SERVICE_AUTO_START
%NSSM_EXE% set %SERVICE_SERVER% AppStdout "%LOG_DIR%\server-stdout.log"
%NSSM_EXE% set %SERVICE_SERVER% AppStderr "%LOG_DIR%\server-stderr.log"
%NSSM_EXE% set %SERVICE_SERVER% AppStdoutCreationDisposition 4
%NSSM_EXE% set %SERVICE_SERVER% AppStderrCreationDisposition 4
:: Rotacion de logs NSSM: 10 MB por archivo
%NSSM_EXE% set %SERVICE_SERVER% AppRotateFiles 1
%NSSM_EXE% set %SERVICE_SERVER% AppRotateBytes 10485760
:: Shutdown graceful via Ctrl+C (5s) antes de forzar kill
%NSSM_EXE% set %SERVICE_SERVER% AppStopMethodConsole 5000
%NSSM_EXE% set %SERVICE_SERVER% AppStopMethodWindow 0
%NSSM_EXE% set %SERVICE_SERVER% AppStopMethodThreads 0
:: Auto-restart tras fallo (5s de espera)
%NSSM_EXE% set %SERVICE_SERVER% AppRestartDelay 5000

:: --------------------------------------------------------------------------
:: bot-listener — Telethon listener
:: --------------------------------------------------------------------------
echo.
echo Instalando servicio: %SERVICE_LISTENER%...

%NSSM_EXE% install %SERVICE_LISTENER% "%PYTHON_EXE%"
%NSSM_EXE% set %SERVICE_LISTENER% AppParameters src\listener\telegram_listener.py
%NSSM_EXE% set %SERVICE_LISTENER% AppDirectory "%PROJECT_DIR%"
%NSSM_EXE% set %SERVICE_LISTENER% AppEnvironmentExtra "PYTHONPATH=%PROJECT_DIR%"
%NSSM_EXE% set %SERVICE_LISTENER% DisplayName "Bot Trading — Telegram Listener"
%NSSM_EXE% set %SERVICE_LISTENER% Description "Listener Telethon que escucha canales Telegram y envia senales al server"
%NSSM_EXE% set %SERVICE_LISTENER% Start SERVICE_AUTO_START
%NSSM_EXE% set %SERVICE_LISTENER% AppStdout "%LOG_DIR%\listener-stdout.log"
%NSSM_EXE% set %SERVICE_LISTENER% AppStderr "%LOG_DIR%\listener-stderr.log"
%NSSM_EXE% set %SERVICE_LISTENER% AppStdoutCreationDisposition 4
%NSSM_EXE% set %SERVICE_LISTENER% AppStderrCreationDisposition 4
%NSSM_EXE% set %SERVICE_LISTENER% AppRotateFiles 1
%NSSM_EXE% set %SERVICE_LISTENER% AppRotateBytes 10485760
%NSSM_EXE% set %SERVICE_LISTENER% AppStopMethodConsole 5000
%NSSM_EXE% set %SERVICE_LISTENER% AppStopMethodWindow 0
%NSSM_EXE% set %SERVICE_LISTENER% AppStopMethodThreads 0
%NSSM_EXE% set %SERVICE_LISTENER% AppRestartDelay 5000

:: --------------------------------------------------------------------------
:: Arrancar servicios
:: --------------------------------------------------------------------------
echo.
echo Arrancando servicios...
%NSSM_EXE% start %SERVICE_SERVER%
%NSSM_EXE% start %SERVICE_LISTENER%

:: --------------------------------------------------------------------------
:: Verificar estado
:: --------------------------------------------------------------------------
echo.
echo Estado de los servicios:
%NSSM_EXE% status %SERVICE_SERVER%
%NSSM_EXE% status %SERVICE_LISTENER%

echo.
echo ============================================================
echo Instalacion completada.
echo Para verificar: nssm status bot-server / bot-listener
echo Para detener:   nssm stop bot-server / bot-listener
echo Para desinstalar: nssm remove bot-server confirm
echo Logs en: %LOG_DIR%
echo ============================================================
pause
