#!/usr/bin/env python3
"""
scripts/analyze_outcomes.py

Cruza las señales parseadas (data/gold_vip_history.csv) con los mensajes
crudos del canal para determinar si cada señal alcanzó TP1/TP2/TP3 o fue SL.

Prerequisito:
    python scripts/analyze_history.py   (genera data/gold_vip_history.csv)

Uso:
    python scripts/analyze_outcomes.py

Salida:
    data/gold_vip_outcomes.csv
    Resumen estadístico en stdout
"""
import asyncio
import bisect
import csv
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from telethon import TelegramClient

# ── Config ─────────────────────────────────────────────────────────────────
CHANNEL_ID     = -1003224347994
CSV_SIGNALS    = PROJECT_ROOT / "data" / "gold_vip_history.csv"
CSV_OUTCOMES   = PROJECT_ROOT / "data" / "gold_vip_outcomes.csv"
OUTCOME_WINDOW = timedelta(hours=24)

CSV_OUT_COLUMNS = [
    "date", "action", "symbol", "entry_type", "entry_price",
    "sl", "tp1", "tp2", "tp3", "outcome",
]

try:
    API_ID   = int(os.environ["TELEGRAM_API_ID"])
    API_HASH = os.environ["TELEGRAM_API_HASH"]
except KeyError as e:
    print(f"[ERROR] Variable de entorno faltante: {e}")
    print("        Activa el venv y asegúrate de que .env esté completo.")
    sys.exit(1)

SESSION = os.environ.get("TELEGRAM_SESSION", "bot_session")


# ── Helpers de precio ──────────────────────────────────────────────────────

def pip_size(symbol: str) -> float:
    s = symbol.upper()
    if "XAU" in s or "GOLD" in s: return 1.0
    if "JPY" in s:                 return 0.01
    return 0.0001


def exec_price(row: dict) -> float | None:
    """
    Precio de ejecución efectivo — lo que el EA usa para entrar:
      LIMIT  → precio exacto
      RANGE  → midpoint (range_high + range_low) / 2
      MARKET → None (precio desconocido)
    """
    etype = row.get("entry_type", "")
    if etype == "LIMIT":
        v = row.get("entry_price", "")
        return float(v) if v else None
    if etype == "RANGE":
        lo = row.get("range_low", "")
        hi = row.get("range_high", "")
        return (float(lo) + float(hi)) / 2.0 if lo and hi else None
    return None


def _float(v: str) -> float | None:
    """Convierte string a float; devuelve None si está vacío."""
    return float(v) if v else None


# ── Clasificación de outcomes ──────────────────────────────────────────────

def _has_tp(text: str, label: str) -> bool:
    """True si el mensaje indica que ese TP fue alcanzado."""
    t = text.lower()
    has_label     = label.lower() in t
    has_indicator = (
        "✅" in text or "✔" in text or
        "hit" in t   or "reached" in t or "alcanzado" in t
    )
    return has_label and has_indicator


def _has_sl_hit(text: str) -> bool:
    """True si el mensaje indica que el stop loss fue tocado."""
    t = text.lower()
    # Mensaje exactamente "SL" (con posibles espacios)
    if re.fullmatch(r'\s*sl\s*', t):
        return True
    # "SL" acompañado de indicador de pérdida
    if re.search(r'\bsl\b', t) and (
        "hit" in t or "❌" in text or "stop" in t or "loss" in t
    ):
        return True
    return False


def _has_cancelled(text: str) -> bool:
    """True si el mensaje indica cancelación o invalidación de la señal."""
    t = text.lower()
    # Necesita alguna referencia a SL o cancelación explícita
    if "cancel" in t:
        return True
    if re.search(r'\bsl\b', t) and any(
        w in t for w in ("seguimos", "analizando", "invalida", "cerrar", "wait")
    ):
        return True
    return False


def classify_outcome(window_texts: list[str]) -> str:
    """
    Determina el mejor outcome de una señal dados los mensajes de su ventana.

    Prioridad de detección: TP3 > TP2 > TP1 > SL > CANCELLED > UNKNOWN

    Nota: un mensaje "TP2 ✅" en la ventana implica que TP2 fue el mejor resultado
    explícitamente informado por el canal en esas 24h; no necesariamente que TP1
    también fue alcanzado (el canal puede informarlos por separado).
    La prioridad garantiza que tomamos el outcome más favorable encontrado.
    """
    tp3 = tp2 = tp1 = sl = cancelled = False

    for text in window_texts:
        if _has_tp(text, "tp3"):   tp3       = True
        if _has_tp(text, "tp2"):   tp2       = True
        if _has_tp(text, "tp1"):   tp1       = True
        if _has_sl_hit(text):      sl        = True
        if _has_cancelled(text):   cancelled = True

    if tp3:       return "TP3"
    if tp2:       return "TP2"
    if tp1:       return "TP1"
    if sl:        return "SL"
    if cancelled: return "CANCELLED"
    return "UNKNOWN"


# ── Descarga del historial ─────────────────────────────────────────────────

async def download_messages(client: TelegramClient) -> list[tuple[datetime, str]]:
    """
    Descarga todos los mensajes del canal con texto.
    Devuelve lista de (datetime UTC, texto) ordenada cronológicamente.
    Usa get_messages() con offset_id para paginar desde el más reciente
    hacia el más antiguo.
    """
    channel = await client.get_entity(CHANNEL_ID)
    print(f"Canal: {getattr(channel, 'title', str(CHANNEL_ID))}")

    raw       = []
    offset_id = 0
    page      = 0

    while True:
        batch = await client.get_messages(channel, limit=100, offset_id=offset_id)
        if not batch:
            break
        for msg in batch:
            if msg.text and msg.text.strip():
                raw.append((msg.date, msg.text))
        offset_id = batch[-1].id
        page += 1
        print(f"  Página {page:4d} — acumulado: {len(raw):,} msgs con texto", end="\r")

    print(f"\nDescarga completada: {len(raw):,} mensajes con texto.")
    raw.sort(key=lambda x: x[0])  # cronológico ascendente
    return raw


# ── Carga del CSV de señales ───────────────────────────────────────────────

def load_signals() -> list[dict]:
    if not CSV_SIGNALS.exists():
        print(f"[ERROR] No se encuentra {CSV_SIGNALS}")
        print("        Ejecuta primero:  python scripts/analyze_history.py")
        sys.exit(1)
    with CSV_SIGNALS.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # Parsear fechas como UTC-aware para poder comparar con Telethon
    for row in rows:
        row["_dt"] = datetime.strptime(
            row["date"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
    print(f"Señales cargadas: {len(rows)} (desde {CSV_SIGNALS.name})")
    return rows


# ── Main ───────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=== analyze_outcomes.py — GOLD VIP 2.0 ===\n")

    signals = load_signals()

    print("\nDescargando historial del canal...")
    session_path = str(PROJECT_ROOT / SESSION)
    client       = TelegramClient(session_path, API_ID, API_HASH)

    async with client:
        messages = await download_messages(client)

    # Índice de tiempos para búsqueda binaria O(log n) por señal
    msg_times = [dt for dt, _ in messages]

    print("\nClasificando outcomes...")
    results = []

    for sig in signals:
        sig_dt = sig["_dt"]
        end_dt = sig_dt + OUTCOME_WINDOW

        # bisect_right(sig_dt) → primer mensaje POSTERIOR a la señal
        lo = bisect.bisect_right(msg_times, sig_dt)
        hi = bisect.bisect_right(msg_times, end_dt)

        window_texts = [messages[i][1] for i in range(lo, hi)]
        outcome      = classify_outcome(window_texts)

        ep = exec_price(sig)  # precio de ejecución efectivo (midpoint para RANGE)

        results.append({
            "date":        sig["date"],
            "action":      sig["action"],
            "symbol":      sig.get("symbol", ""),
            "entry_type":  sig.get("entry_type", ""),
            # entry_price = precio de ejecución real (midpoint RANGE | exacto LIMIT)
            "entry_price": f"{ep:.5f}" if ep is not None else "",
            "sl":          sig["sl"],
            "tp1":         sig["tp1"],
            "tp2":         sig["tp2"],
            "tp3":         sig["tp3"],
            "outcome":     outcome,
            # Internos para estadísticas (no van al CSV)
            "_exec":       ep,
            "_sl":         _float(sig["sl"]),
            "_tp1":        _float(sig["tp1"]),
            "_tp2":        _float(sig["tp2"]),
            "_tp3":        _float(sig["tp3"]),
            "_symbol":     sig.get("symbol", "XAUUSD"),
        })

    # ── Exportar CSV ──────────────────────────────────────────────────────
    CSV_OUTCOMES.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUTCOMES.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_OUT_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r[k] for k in CSV_OUT_COLUMNS})
    print(f"CSV exportado: {CSV_OUTCOMES}  ({len(results)} filas)\n")

    _print_stats(results)


# ── Estadísticas ───────────────────────────────────────────────────────────

def _print_stats(results: list[dict]) -> None:
    n      = len(results)
    counts = Counter(r["outcome"] for r in results)
    sep    = "─" * 54

    def pct(k: str) -> float:
        return counts[k] / n * 100 if n else 0.0

    print(sep)
    print("  OUTCOMES — GOLD VIP 2.0")
    print(sep)
    print(f"  Total señales analizadas : {n:,}")
    print(sep)
    print(f"  TP1 hit      : {counts['TP1']:4d}   ({pct('TP1'):5.1f}%)")
    print(f"  TP2 hit      : {counts['TP2']:4d}   ({pct('TP2'):5.1f}%)")
    print(f"  TP3 hit      : {counts['TP3']:4d}   ({pct('TP3'):5.1f}%)")
    print(f"  SL hit       : {counts['SL']:4d}   ({pct('SL'):5.1f}%)")
    print(f"  Canceladas   : {counts['CANCELLED']:4d}   ({pct('CANCELLED'):5.1f}%)")
    print(f"  Sin resultado: {counts['UNKNOWN']:4d}   ({pct('UNKNOWN'):5.1f}%)")

    # Win rate (sobre señales con resultado conocido)
    n_tp    = counts["TP1"] + counts["TP2"] + counts["TP3"]
    n_sl    = counts["SL"]
    n_known = n_tp + n_sl

    win_rate = n_tp / n_known if n_known else 0.0
    print(sep)
    print(f"  Win rate (TP1 o mejor)   : {win_rate * 100:.1f}%  (sobre {n_known} conocidos)")

    # ── Expectativa matemática ────────────────────────────────────────────
    # Calcula ganancias/pérdidas reales en pips usando el precio de ejecución
    # efectivo (midpoint para RANGE, exacto para LIMIT).
    profit_pips: list[float] = []
    loss_pips:   list[float] = []

    for r in results:
        ep  = r["_exec"]
        oc  = r["outcome"]
        sym = r["_symbol"]

        if ep is None:
            continue  # MARKET sin precio de referencia

        ps     = pip_size(sym)
        tp_val = {"TP1": r["_tp1"], "TP2": r["_tp2"], "TP3": r["_tp3"]}.get(oc)

        if tp_val is not None:
            profit_pips.append(abs(ep - tp_val) / ps)
        elif oc == "SL" and r["_sl"] is not None:
            loss_pips.append(abs(ep - r["_sl"]) / ps)

    def avg(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    avg_profit = avg(profit_pips)
    avg_loss   = avg(loss_pips)
    expectation = win_rate * avg_profit - (1.0 - win_rate) * avg_loss

    print(sep)
    print("  Expectativa matemática (señales con precio de ejecución conocido):")
    print(f"  Ganancia media (TP wins) : {avg_profit:+7.1f} pips  (n={len(profit_pips)})")
    print(f"  Pérdida media  (SL hits) : {avg_loss:+7.1f} pips  (n={len(loss_pips)})")
    print(f"  Expectativa por trade    : {expectation:+7.1f} pips")
    print(sep)

    if n_known >= 10:
        if expectation > 0:
            verdict = "Sistema con edge POSITIVO en el histórico."
        elif expectation < 0:
            verdict = "Sistema con edge NEGATIVO en el histórico."
        else:
            verdict = "Sistema NEUTRAL (break-even histórico)."
        print(f"  => {verdict}")
        print(sep)

    if counts["UNKNOWN"] > 0:
        pct_unk = pct("UNKNOWN")
        print(f"  NOTA: {counts['UNKNOWN']} señales sin resultado ({pct_unk:.0f}%).")
        if pct_unk > 30:
            print("        Porcentaje alto — el canal puede no informar todos los outcomes.")
            print("        Los stats de win rate pueden estar sesgados.")
    print(sep)
    print(f"  CSV: {CSV_OUTCOMES}")
    print(sep)


if __name__ == "__main__":
    asyncio.run(main())
