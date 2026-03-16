@echo off
setlocal EnableDelayedExpansion

:: ============================================================================
:: backup_db.bat
:: Copia dedup.db y el archivo .session a una carpeta con timestamp.
::
:: Ejecutar diariamente via Windows Task Scheduler.
::
:: Configurar en Task Scheduler:
::   Programa  : C:\bot\telegram-mt5-bot\scripts\backup_db.bat
::   Directorio: C:\bot\telegram-mt5-bot
::   Disparador: diario a las 04:00 AM
::
:: Retención: mantiene los últimos 7 backups (elimina los más viejos).
:: ============================================================================

:: --------------------------------------------------------------------------
:: CONFIGURACION — ajustar si el path del proyecto difiere
:: --------------------------------------------------------------------------
set PROJECT_DIR=C:\bot\telegram-mt5-bot
set BACKUP_ROOT=%PROJECT_DIR%\backups
set SESSION_NAME=bot_session
set RETENTION_DAYS=7

:: --------------------------------------------------------------------------
:: Crear directorio de backups si no existe
:: --------------------------------------------------------------------------
if not exist "%BACKUP_ROOT%" mkdir "%BACKUP_ROOT%"

:: --------------------------------------------------------------------------
:: Timestamp para el directorio de este backup
:: --------------------------------------------------------------------------
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set DT=%%I
set TIMESTAMP=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%_%DT:~8,2%-%DT:~10,2%

set BACKUP_DIR=%BACKUP_ROOT%\%TIMESTAMP%
mkdir "%BACKUP_DIR%"

echo [%TIMESTAMP%] Iniciando backup...

:: --------------------------------------------------------------------------
:: Backup dedup.db (y archivos WAL si existen)
:: --------------------------------------------------------------------------
set DB_SRC=%PROJECT_DIR%\data\dedup.db

if exist "%DB_SRC%" (
    copy /Y "%DB_SRC%" "%BACKUP_DIR%\dedup.db" >nul
    echo [OK] dedup.db copiado
) else (
    echo [WARN] dedup.db no encontrado en %DB_SRC%
)

if exist "%DB_SRC%-shm" (
    copy /Y "%DB_SRC%-shm" "%BACKUP_DIR%\dedup.db-shm" >nul
)
if exist "%DB_SRC%-wal" (
    copy /Y "%DB_SRC%-wal" "%BACKUP_DIR%\dedup.db-wal" >nul
)

:: --------------------------------------------------------------------------
:: Backup del archivo .session (autenticacion Telethon)
:: --------------------------------------------------------------------------
set SESSION_SRC=%PROJECT_DIR%\%SESSION_NAME%.session

if exist "%SESSION_SRC%" (
    copy /Y "%SESSION_SRC%" "%BACKUP_DIR%\%SESSION_NAME%.session" >nul
    echo [OK] %SESSION_NAME%.session copiado
) else (
    echo [WARN] Archivo .session no encontrado: %SESSION_SRC%
)

:: --------------------------------------------------------------------------
:: Eliminar backups mas antiguos que RETENTION_DAYS dias
:: --------------------------------------------------------------------------
echo Limpiando backups de mas de %RETENTION_DAYS% dias...
forfiles /P "%BACKUP_ROOT%" /D -%RETENTION_DAYS% /C "cmd /c if @isdir==TRUE rd /s /q @path" >nul 2>&1

echo [%TIMESTAMP%] Backup completado en: %BACKUP_DIR%
