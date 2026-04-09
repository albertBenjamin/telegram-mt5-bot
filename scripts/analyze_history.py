#!/usr/bin/env python3
"""
scripts/analyze_history.py

Descarga el historial completo del canal GOLD VIP 2.0, parsea cada mensaje
con el parser existente y exporta las señales válidas a CSV.

Uso:
    python scripts/analyze_history.py

Salida:
    data/gold_vip_history.csv
    Resumen estadístico en stdout
"""
import asyncio
import csv
import os
import sys
from collections import Counter
from datetime import timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Deps ───────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from telethon import TelegramClient

from src.parser.models import NoOpSignal, ParseError, ValidationError
from src.parser.signal_parser import process

# ── Config ─────────────────────────────────────────────────────────────────
CHANNEL_ID  = -1003224347994
CSV_PATH    = PROJECT_ROOT / "data" / "gold_vip_history.csv"
CSV_COLUMNS = [
    "date", "action", "symbol", "entry_type",
    "entry_price", "range_low", "range_high",
    "sl", "tp1", "tp2", "tp3",
]

try:
    API_ID   = int(os.environ["TELEGRAM_API_ID"])
    API_HASH = os.environ["TELEGRAM_API_HASH"]
except KeyError as e:
    print(f"[ERROR] Variable de entorno faltante: {e}")
    print("        Activa el venv y asegúrate de que .env esté completo.")
    sys.exit(1)

SESSION = os.environ.get("TELEGRAM_SESSION", "bot_session")

_raw_whitelist = os.environ.get("WHITELIST_SYMBOLS", "")
WHITELIST_SYMBOLS: frozenset[str] | None = (
    frozenset(s.strip() for s in _raw_whitelist.split(",") if s.strip())
    if _raw_whitelist else None
)


# ── Helpers ────────────────────────────────────────────────────────────────

def pip_size(symbol: str) -> float:
    """Tamaño de un pip según el símbolo (para calcular distancias)."""
    s = symbol.upper()
    if "XAU" in s or "GOLD" in s:
        return 1.0      # XAUUSD: 1 pip = $1
    if "JPY" in s:
        return 0.01     # Pares con JPY
    return 0.0001       # Pares de divisas principales (EUR, GBP, GBP…)


def ref_price(sig) -> float | None:
    """
    Precio de referencia para calcular distancia al SL/TPs.
    Consistente con la lógica de validación del parser (CLAUDE.md):
      SELL → range_high | LIMIT price
      BUY  → range_low  | LIMIT price
      MARKET → None (sin precio disponible)
    """
    etype = sig.entry.type.value
    if etype == "LIMIT":
        return sig.entry.price
    if etype == "RANGE":
        return sig.entry.range_high if sig.action.value == "SELL" else sig.entry.range_low
    return None  # MARKET


# ── Descarga del historial ─────────────────────────────────────────────────

async def download_history(client: TelegramClient) -> list:
    """
    Descarga todos los mensajes del canal iterando por páginas de 100.
    Usa get_messages() con offset_id para paginar desde el más reciente
    hacia el más antiguo hasta agotar el historial disponible.
    """
    channel = await client.get_entity(CHANNEL_ID)
    print(f"Canal: {getattr(channel, 'title', str(CHANNEL_ID))}")

    all_messages = []
    offset_id = 0
    page = 0

    while True:
        batch = await client.get_messages(channel, limit=100, offset_id=offset_id)
        if not batch:
            break
        all_messages.extend(batch)
        offset_id = batch[-1].id
        page += 1
        print(f"  Página {page:4d} — {len(batch):3d} msgs  (acumulado: {len(all_messages):,})", end="\r")

    print(f"\nDescarga completada: {len(all_messages):,} mensajes totales.")
    return all_messages


# ── Parseo ─────────────────────────────────────────────────────────────────

def parse_messages(messages: list) -> tuple[list, dict]:
    """
    Pasa cada mensaje por el parser. Devuelve:
      signals  → lista de (datetime, ParsedSignal) en orden cronológico
      counters → dict con totales para las estadísticas
    """
    signals     = []
    n_empty     = 0
    n_noop      = 0
    n_parse_err = 0
    n_valid_err = 0

    for msg in messages:
        if not msg.text or not msg.text.strip():
            n_empty += 1
            continue
        try:
            result = process(
                text=msg.text,
                channel_id=msg.chat_id,
                message_id=msg.id,
                allowed_symbols=WHITELIST_SYMBOLS,
            )
        except ParseError:
            n_parse_err += 1
            continue
        except ValidationError:
            n_valid_err += 1
            continue

        if isinstance(result, NoOpSignal):
            n_noop += 1
            continue

        signals.append((msg.date, result))

    # Ordenar cronológicamente (get_messages devuelve el más reciente primero)
    signals.sort(key=lambda x: x[0])

    counters = {
        "total":          len(messages),
        "empty":          n_empty,
        "noop":           n_noop,
        "parse_error":    n_parse_err,
        "valid_error":    n_valid_err,
        "signals":        len(signals),
    }
    return signals, counters


# ── Exportar CSV ───────────────────────────────────────────────────────────

def export_csv(signals: list) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for date, sig in signals:
            tps = sig.tps
            writer.writerow({
                "date":        date.strftime("%Y-%m-%d %H:%M:%S"),
                "action":      sig.action.value,
                "symbol":      sig.symbol,
                "entry_type":  sig.entry.type.value,
                "entry_price": sig.entry.price   if sig.entry.price      is not None else "",
                "range_low":   sig.entry.range_low  if sig.entry.range_low  is not None else "",
                "range_high":  sig.entry.range_high if sig.entry.range_high is not None else "",
                "sl":          sig.sl,
                "tp1":         tps[0] if len(tps) > 0 else "",
                "tp2":         tps[1] if len(tps) > 1 else "",
                "tp3":         tps[2] if len(tps) > 2 else "",
            })
    print(f"CSV exportado: {CSV_PATH}  ({len(signals)} filas)")


# ── Estadísticas ───────────────────────────────────────────────────────────

def print_stats(signals: list, counters: dict) -> None:
    n        = counters["signals"]
    total    = counters["total"]
    n_text   = total - counters["empty"]
    sep      = "─" * 52

    print(f"\n{sep}")
    print("  ESTADÍSTICAS — GOLD VIP 2.0")
    print(sep)

    # Parseo general
    parse_rate = n / n_text * 100 if n_text else 0
    print(f"  Mensajes totales       : {total:,}")
    print(f"  Con texto              : {n_text:,}")
    print(f"  Señales válidas        : {n:,}")
    print(f"  NoOp (mov SL, TP hit…) : {counters['noop']:,}")
    print(f"  ParseError             : {counters['parse_error']:,}")
    print(f"  ValidationError        : {counters['valid_error']:,}")
    print(f"  Tasa de parseo         : {parse_rate:.1f}%")

    if n == 0:
        print(sep)
        print("  Sin señales válidas para continuar el análisis.")
        print(sep)
        return

    # Rango de fechas
    dates    = [d for d, _ in signals]
    date_min = min(dates)
    date_max = max(dates)
    print(sep)
    print(f"  Rango de fechas        : {date_min.strftime('%Y-%m-%d')}  →  {date_max.strftime('%Y-%m-%d')}")

    # BUY vs SELL
    actions    = Counter(sig.action.value for _, sig in signals)
    buy_count  = actions["BUY"]
    sell_count = actions["SELL"]
    print(sep)
    print(f"  BUY                    : {buy_count:,}  ({buy_count / n * 100:.1f}%)")
    print(f"  SELL                   : {sell_count:,}  ({sell_count / n * 100:.1f}%)")

    # Horas UTC con más señales
    hour_counter = Counter(
        d.astimezone(timezone.utc).hour for d, _ in signals
    )
    top_hours = hour_counter.most_common(5)
    max_count = top_hours[0][1] if top_hours else 1
    bar_scale = 20 / max_count
    print(sep)
    print("  Top 5 horas UTC con más señales:")
    for hour, count in top_hours:
        bar = "█" * int(count * bar_scale)
        print(f"    {hour:02d}:00   {count:4d}  {bar}")

    # Distancias en pips (excluye MARKET — sin precio de referencia)
    sl_d, tp1_d, tp2_d, tp3_d = [], [], [], []
    for _, sig in signals:
        rp = ref_price(sig)
        if rp is None:
            continue
        ps = pip_size(sig.symbol)
        sl_d.append(abs(rp - sig.sl) / ps)
        tps = sig.tps
        if len(tps) > 0: tp1_d.append(abs(rp - tps[0]) / ps)
        if len(tps) > 1: tp2_d.append(abs(rp - tps[1]) / ps)
        if len(tps) > 2: tp3_d.append(abs(rp - tps[2]) / ps)

    def avg(lst: list) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    n_range_limit = len(sl_d)
    print(sep)
    print(f"  Distancias promedio (RANGE + LIMIT, n={n_range_limit})")
    print(f"  SL   : {avg(sl_d):6.1f} pips")
    print(f"  TP1  : {avg(tp1_d):6.1f} pips  (n={len(tp1_d)})")
    print(f"  TP2  : {avg(tp2_d):6.1f} pips  (n={len(tp2_d)})")
    print(f"  TP3  : {avg(tp3_d):6.1f} pips  (n={len(tp3_d)})")

    # Nota de referencia para XAUUSD
    symbols_used = Counter(sig.symbol for _, sig in signals)
    if symbols_used:
        print(sep)
        print("  Símbolos:")
        for sym, cnt in symbols_used.most_common():
            print(f"    {sym:<14} {cnt:4d}  ({cnt / n * 100:.1f}%)")

    print(sep)
    print(f"  CSV: {CSV_PATH}")
    print(sep)


# ── Main ───────────────────────────────────────────────────────────────────

async def main() -> None:
    session_path = str(PROJECT_ROOT / SESSION)
    client = TelegramClient(session_path, API_ID, API_HASH)

    print("=== analyze_history.py — GOLD VIP 2.0 ===")

    async with client:
        messages = await download_history(client)

    print("\nParsando mensajes...")
    signals, counters = parse_messages(messages)

    export_csv(signals)
    print_stats(signals, counters)


if __name__ == "__main__":
    asyncio.run(main())
