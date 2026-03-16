"""
E2-6 — Tests unitarios del parser.

Estructura:
  - TestProcessValid        (8 casos)  señales bien formadas → ParsedSignal
  - TestParseError         (12 casos)  formato inválido → ParseError
  - TestValidationError     (8 casos)  precios incoherentes → ValidationError
  - TestNoOp                (7 casos)  mensajes de actualización → NoOpSignal
  - TestSignalId            (3 casos)  E2-5: SHA256(channel_id:message_id)
  - TestReferencePrice      (4 casos)  precio de referencia RANGE vs LIMIT
  - TestSymbolWhitelist     (2 casos)  allowed_symbols

Total: 44 casos
"""
import hashlib

import pytest

from src.parser.models import (
    Action,
    EntryType,
    NoOpSignal,
    ParseError,
    ValidationError,
)
from src.parser.signal_parser import _generate_signal_id, process, validate, parse

WHITELIST = frozenset({"XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY"})


# ===========================================================================
# Señales válidas — process() debe devolver ParsedSignal sin excepciones
# ===========================================================================

@pytest.mark.parametrize("text,action,entry_type,sl,tp0,tps_count", [
    # SELL rango
    ("SELL XAUUSD 5181-5185 / SL 5189 / TP 5179 5177 5174",
     Action.SELL, EntryType.RANGE, 5189.0, 5179.0, 3),
    # BUY market
    ("BUY EURUSD / SL 1.0800 / TP 1.0900",
     Action.BUY, EntryType.MARKET, 1.08, 1.09, 1),
    # SELL limit
    ("SELL GBPUSD 1.2650 / SL 1.2680 / TP 1.2620",
     Action.SELL, EntryType.LIMIT, 1.268, 1.262, 1),
    # BUY limit con separador pipe
    ("BUY GBPJPY 191.50 | SL 191.00 | TP 192.50",
     Action.BUY, EntryType.LIMIT, 191.0, 192.5, 1),
    # BUY rango con múltiples TPs
    ("BUY XAUUSD 2000-2005 / SL 1990 / TP 2015 2020 2025",
     Action.BUY, EntryType.RANGE, 1990.0, 2015.0, 3),
    # SELL market
    ("SELL USDJPY / SL 155.00 / TP 154.00",
     Action.SELL, EntryType.MARKET, 155.0, 154.0, 1),
    # Normalización: mayúsculas + espacios extra
    ("  sell   xauusd  5181-5185  /  sl  5189  /  tp  5179  5177  ",
     Action.SELL, EntryType.RANGE, 5189.0, 5179.0, 2),
    # BUY limit con múltiples TPs
    ("BUY GBPUSD 1.2600 / SL 1.2550 / TP 1.2650 1.2700",
     Action.BUY, EntryType.LIMIT, 1.255, 1.265, 2),
    # Formato canal real: separador \n\n, TPs con TP prefix repetido
    ("SELL XAUUSD 5138-5142\n\nSL 5146\n\nTP 5136\nTP 5134\nTP 5131",
     Action.SELL, EntryType.RANGE, 5146.0, 5136.0, 3),
    # Formato sin separador /|, TPs en una línea
    ("SELL XAUUSD 5138-5142 SL 5146 TP 5136 5134 5131",
     Action.SELL, EntryType.RANGE, 5146.0, 5136.0, 3),
])
def test_process_valid(text, action, entry_type, sl, tp0, tps_count):
    signal = process(text)
    assert signal.action == action
    assert signal.entry.type == entry_type
    assert signal.sl == pytest.approx(sl)
    assert signal.tps[0] == pytest.approx(tp0)
    assert len(signal.tps) == tps_count
    assert signal.raw_message == text


# ===========================================================================
# ParseError — formato inválido o incompleto
# ===========================================================================

@pytest.mark.parametrize("text", [
    # Sin TP
    "SELL XAUUSD / SL 5189",
    # Sin SL
    "SELL XAUUSD / TP 5179",
    # Falta keyword SL (solo número)
    "SELL XAUUSD 5181-5185 / 5189 / TP 5179",
    # Sin símbolo
    "BUY / SL 1.0800 / TP 1.0900",
    # Acción inválida
    "LONG EURUSD / SL 1.0800 / TP 1.0900",
    # Texto libre
    "compra oro cerca de 2000",
    # Mensaje vacío
    "",
    # Símbolo demasiado corto (< 3 chars)
    "BUY EU / SL 1.08 / TP 1.09",
    # Símbolo demasiado largo (> 10 chars)
    "BUY EURUSDGBPJPY1 / SL 1.08 / TP 1.09",
    # Prefijo extra → fullmatch rechaza (re.match() lo aceptaría)
    "Earlier signal: SELL XAUUSD / SL 1.2680 / TP 1.2620",
    # Sufijo extra → fullmatch rechaza (re.match() lo aceptaría)
    "SELL XAUUSD / SL 1.2680 / TP 1.2620 old signal",
    # Señal parcial al final del mensaje
    "check the signal SELL XAUUSD 5181-5185 / SL 5189 / TP 5179",
])
def test_process_parse_error(text):
    with pytest.raises(ParseError):
        process(text)


# ===========================================================================
# ValidationError — precios incoherentes con la dirección
# ===========================================================================

@pytest.mark.parametrize("text", [
    # SELL LIMIT: SL debe ser > entry, aquí SL < entry
    "SELL XAUUSD 5185 / SL 5179 / TP 5170",
    # BUY LIMIT: SL debe ser < entry, aquí SL > entry
    "BUY EURUSD 1.0900 / SL 1.0950 / TP 1.1000",
    # SELL RANGE: TP[0] debe ser < range_high, aquí TP > range_high
    "SELL XAUUSD 5181-5185 / SL 5189 / TP 5190",
    # SELL LIMIT: TP[0] debe ser < entry
    "SELL GBPUSD 1.2650 / SL 1.2680 / TP 1.2700",
    # BUY MARKET: SL debe ser < TP[0]
    "BUY EURUSD / SL 1.0950 / TP 1.0800",
    # SELL MARKET: SL debe ser > TP[0]
    "SELL EURUSD / SL 1.0800 / TP 1.0900",
    # SL == entry (condición estricta, no se cumple >)
    "BUY XAUUSD 2000 / SL 2000 / TP 2010",
    # SL == entry (condición estricta, no se cumple <)
    "SELL XAUUSD 5185 / SL 5185 / TP 5179",
])
def test_process_validation_error(text):
    with pytest.raises(ValidationError):
        process(text)


# ===========================================================================
# NoOpSignal — mensajes de actualización, no son errores
# ===========================================================================

@pytest.mark.parametrize("text,expected_reason", [
    ("TP1 hit",                   "TP1 HIT"),
    ("TP2 hit, waiting TP3",      "TP2 HIT"),
    ("Move SL to 5180",           "MOVE SL"),
    ("Signal cancelled",          "CANCEL"),
    ("Trade closed",              "CLOSE"),
    ("Move to breakeven",         "BREAKEVEN"),
    ("Partial close 50%",         "PARTIAL"),
])
def test_process_noop(text, expected_reason):
    result = process(text)
    assert isinstance(result, NoOpSignal)
    assert expected_reason in result.reason


# ===========================================================================
# E2-5 — signal_id = SHA256(channel_id:message_id)
# ===========================================================================

class TestSignalId:
    def test_signal_id_is_sha256_hex(self):
        signal = process("SELL XAUUSD / SL 5189 / TP 5179", channel_id=123, message_id=456)
        expected = hashlib.sha256(b"123:456").hexdigest()
        assert signal.signal_id == expected

    def test_signal_id_is_64_chars(self):
        signal = process("BUY EURUSD / SL 1.08 / TP 1.09", channel_id=1, message_id=1)
        assert len(signal.signal_id) == 64

    def test_signal_id_is_deterministic(self):
        s1 = process("BUY EURUSD / SL 1.08 / TP 1.09", channel_id=99, message_id=7)
        s2 = process("BUY EURUSD / SL 1.08 / TP 1.09", channel_id=99, message_id=7)
        assert s1.signal_id == s2.signal_id

    def test_source_channel(self):
        signal = process("BUY EURUSD / SL 1.08 / TP 1.09", channel_id=-1003224347994)
        assert signal.source_channel == "-1003224347994"

    def test_generate_signal_id_helper(self):
        result = _generate_signal_id(-1001234567890, 42)
        expected = hashlib.sha256(b"-1001234567890:42").hexdigest()
        assert result == expected


# ===========================================================================
# Precio de referencia en RANGE (especificación usuario)
# ===========================================================================

class TestReferencePrice:
    def test_sell_range_uses_range_high(self):
        """SELL RANGE: referencia = range_high → SL > range_high > TP[0]"""
        # range_high=5185, SL=5189 > 5185, TP=5179 < 5185 → válido
        signal = process("SELL XAUUSD 5181-5185 / SL 5189 / TP 5179")
        assert signal.entry.range_high == 5185.0
        assert signal.entry.range_low == 5181.0

    def test_buy_range_uses_range_low(self):
        """BUY RANGE: referencia = range_low → SL < range_low < TP[0]"""
        # range_low=2000, SL=1990 < 2000, TP=2015 > 2000 → válido
        signal = process("BUY XAUUSD 2000-2005 / SL 1990 / TP 2015")
        assert signal.entry.range_low == 2000.0
        assert signal.entry.range_high == 2005.0

    def test_sell_range_invalid_sl_below_range_low(self):
        """SELL RANGE con SL < range_high → ValidationError aunque SL > range_low"""
        # range_high=5185, SL=5183 < 5185 → inválido
        with pytest.raises(ValidationError):
            process("SELL XAUUSD 5181-5185 / SL 5183 / TP 5179")

    def test_buy_range_invalid_tp_below_range_low(self):
        """BUY RANGE con TP < range_low → ValidationError"""
        # range_low=2000, TP=1995 < 2000 → inválido
        with pytest.raises(ValidationError):
            process("BUY XAUUSD 2000-2005 / SL 1990 / TP 1995")


# ===========================================================================
# Whitelist de símbolos
# ===========================================================================

class TestSymbolWhitelist:
    def test_symbol_in_whitelist_ok(self):
        signal = process(
            "SELL XAUUSD / SL 5189 / TP 5179",
            allowed_symbols=WHITELIST,
        )
        assert signal.symbol == "XAUUSD"

    def test_symbol_not_in_whitelist_raises_parse_error(self):
        with pytest.raises(ParseError, match="symbol_not_allowed"):
            process(
                "SELL BTCUSD / SL 90000 / TP 85000",
                allowed_symbols=WHITELIST,
            )
