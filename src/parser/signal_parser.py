"""
Signal parser — E2-1 (regex) + E2-2 (validación) + E2-3 (normalización) + E2-4 (NoOp).

Diseño anti-ReDoS:
- Cuantificadores acotados: {1,10} y {1,8} en lugar de + o *
- Sin cuantificadores anidados: ningún grupo con * o + contiene a su vez * o +
- El bloque TP usa \\s+ obligatorio como separador → el motor no puede retroceder
  entre iteraciones
"""
import hashlib
import re

from .models import (
    Action,
    EntryPrice,
    EntryType,
    NoOpSignal,
    ParsedSignal,
    ParseError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Bloques reutilizables
# ---------------------------------------------------------------------------

# Número: hasta 10 dígitos enteros, hasta 8 decimales — acotado intencionalmente
_NUM = r'\d{1,10}(?:\.\d{1,8})?'

# Separador: / o | con espacios opcionales, o simplemente uno o más espacios
_SEP = r'(?:\s*[/|]\s*|\s+)'

# ---------------------------------------------------------------------------
# Regex principal (E2-1)
# ---------------------------------------------------------------------------
#
# Grupos nombrados:
#   action  → BUY | SELL
#   symbol  → 3-10 letras mayúsculas
#   entry   → opcional: "5181-5185" (rango) o "1.2650" (precio exacto)
#             ausente → MARKET
#   sl      → precio del Stop Loss
#   tps     → uno o más precios de Take Profit separados por espacio
#
# _NUM(?:-_NUM)?  →  rango o precio exacto, sin ambigüedad:
#   el primer _NUM es greedy pero no puede consumir '-' (no está en \d ni en \.)
#   por lo que el motor llega al '-' de forma determinista.
#
# TP: _NUM seguido de (?:\s+_NUM)* — el \\s+ obligatorio garantiza que
#   cada iteración consume al menos un espacio + un número, sin backtracking.

_SIGNAL_RE = re.compile(
    rf'^(?P<action>BUY|SELL)'
    rf'\s+(?P<symbol>[A-Z]{{3,10}})'
    rf'(?:\s+(?P<entry>{_NUM}(?:-{_NUM})?))?'
    rf'{_SEP}SL\s+(?P<sl>{_NUM})'
    rf'{_SEP}TP\s+(?P<tps>{_NUM}(?:\s+(?:TP\s+)?{_NUM})*)$',
    re.ASCII,
)

# ---------------------------------------------------------------------------
# NoOp patterns (E2-4)
# ---------------------------------------------------------------------------
# Se aplica sobre texto normalizado (uppercase).
# re.search() con \\b — no necesita fullmatch porque estos mensajes
# pueden tener texto libre alrededor ("TP1 HIT, waiting TP2", etc.)

_NOOP_RE = re.compile(
    r'\b('
    r'MOVE\s+SL'
    r'|TP\d+\s+HIT'
    r'|CLOSE[D]?'
    r'|CANCEL(?:LED)?'
    r'|BREAKEVEN'
    r'|PARTIAL(?:\s+CLOSE)?'
    r'|ENTRY\s+HIT'
    r')\b',
    re.ASCII,
)


# ---------------------------------------------------------------------------
# Normalización (E2-3)
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """strip + upper + colapsar espacios múltiples a uno solo."""
    return re.sub(r'\s+', ' ', text.strip().upper())


# ---------------------------------------------------------------------------
# Precio de referencia para validación (E2-2)
# ---------------------------------------------------------------------------

def _ref_price(entry: EntryPrice, action: Action) -> float | None:
    """
    Precio de referencia según tipo de entrada y dirección.
    - MARKET  → None (no hay precio de entrada, solo se valida SL vs TP[0])
    - RANGE   → range_high para SELL, range_low para BUY
    - LIMIT   → precio exacto
    """
    if entry.type == EntryType.MARKET:
        return None
    if entry.type == EntryType.RANGE:
        return entry.range_high if action == Action.SELL else entry.range_low
    return entry.price  # LIMIT


# ---------------------------------------------------------------------------
# Validación lógica de precios (E2-2)
# ---------------------------------------------------------------------------

def validate(signal: ParsedSignal) -> None:
    """
    Valida que SL y TPs sean coherentes con la dirección de la operación.

    SELL: SL > ref_price > TP[0]   (SL arriba, TPs abajo)
    BUY:  SL < ref_price < TP[0]   (SL abajo, TPs arriba)

    Para MARKET (sin ref_price) solo se valida SL vs TP[0].

    Raises:
        ValidationError: si alguna condición no se cumple.
    """
    sl = signal.sl
    tp0 = signal.tps[0]
    ref = _ref_price(signal.entry, signal.action)

    if signal.action == Action.SELL:
        if ref is not None and not (sl > ref > tp0):
            raise ValidationError(
                f"SELL inválido: se esperaba SL({sl}) > entry({ref}) > TP[0]({tp0})"
            )
        if ref is None and not (sl > tp0):
            raise ValidationError(
                f"SELL MARKET inválido: se esperaba SL({sl}) > TP[0]({tp0})"
            )
    else:  # BUY
        if ref is not None and not (sl < ref < tp0):
            raise ValidationError(
                f"BUY inválido: se esperaba SL({sl}) < entry({ref}) < TP[0]({tp0})"
            )
        if ref is None and not (sl < tp0):
            raise ValidationError(
                f"BUY MARKET inválido: se esperaba SL({sl}) < TP[0]({tp0})"
            )


# ---------------------------------------------------------------------------
# signal_id (E2-5)
# ---------------------------------------------------------------------------

def _generate_signal_id(channel_id: int, message_id: int) -> str:
    """SHA256(channel_id:message_id) — identificador único e idempotente."""
    raw = f"{channel_id}:{message_id}".encode()
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# API pública unificada
# ---------------------------------------------------------------------------

def process(
    text: str,
    channel_id: int = 0,
    message_id: int = 0,
    allowed_symbols: frozenset[str] | None = None,
) -> ParsedSignal | NoOpSignal:
    """
    Punto de entrada principal para el listener.

    Flujo:
      1. Normalizar texto
      2. Detectar NoOp → devolver NoOpSignal (no es error)
      3. Parsear con regex → ParsedSignal o ParseError
      4. Validar símbolo contra whitelist (si se proporciona)
      5. Validar lógica de precios → ValidationError si incoherente
      6. Asignar signal_id y source_channel

    Args:
        text:            Texto raw del mensaje de Telegram.
        channel_id:      ID numérico del canal (para signal_id).
        message_id:      ID del mensaje (para signal_id).
        allowed_symbols: Whitelist de símbolos. None = sin restricción.

    Raises:
        ParseError:      Formato no reconocido o símbolo no permitido.
        ValidationError: Precios incoherentes con la dirección.
    """
    normalized = _normalize(text)

    # E2-4 — NoOp antes que parse (estos mensajes nunca matchean el regex)
    noop_match = _NOOP_RE.search(normalized)
    if noop_match:
        return NoOpSignal(reason=noop_match.group(0))

    # E2-1 — Parse
    m = _SIGNAL_RE.fullmatch(normalized)
    if m is None:
        raise ParseError(f"no_match: {text!r}")

    action = Action(m.group("action"))
    sl = float(m.group("sl"))
    tps = [float(p) for p in re.findall(_NUM, m.group("tps"))]

    entry_raw = m.group("entry")
    if entry_raw is None:
        entry = EntryPrice(type=EntryType.MARKET)
    elif "-" in entry_raw:
        low_str, high_str = entry_raw.split("-", 1)
        entry = EntryPrice(
            type=EntryType.RANGE,
            range_low=float(low_str),
            range_high=float(high_str),
        )
    else:
        entry = EntryPrice(type=EntryType.LIMIT, price=float(entry_raw))

    symbol = m.group("symbol")

    # Validar símbolo contra whitelist
    if allowed_symbols is not None and symbol not in allowed_symbols:
        raise ParseError(f"symbol_not_allowed: {symbol!r}")

    signal = ParsedSignal(
        action=action,
        symbol=symbol,
        entry=entry,
        sl=sl,
        tps=tps,
        raw_message=text,
        signal_id=_generate_signal_id(channel_id, message_id),
        source_channel=str(channel_id),
    )

    # E2-2 — Validar lógica de precios (fail-closed)
    validate(signal)

    return signal


# parse() se mantiene como función de bajo nivel (útil en tests)
def parse(text: str) -> ParsedSignal:
    """
    Solo parsea, sin validar precios ni detectar NoOps.
    Útil para tests unitarios del regex en aislamiento.

    Raises:
        ParseError: si el texto no encaja con ningún formato conocido.
    """
    normalized = _normalize(text)
    m = _SIGNAL_RE.fullmatch(normalized)
    if m is None:
        raise ParseError(f"no_match: {text!r}")

    action = Action(m.group("action"))
    sl = float(m.group("sl"))
    tps = [float(p) for p in re.findall(_NUM, m.group("tps"))]

    entry_raw = m.group("entry")
    if entry_raw is None:
        entry = EntryPrice(type=EntryType.MARKET)
    elif "-" in entry_raw:
        low_str, high_str = entry_raw.split("-", 1)
        entry = EntryPrice(
            type=EntryType.RANGE,
            range_low=float(low_str),
            range_high=float(high_str),
        )
    else:
        entry = EntryPrice(type=EntryType.LIMIT, price=float(entry_raw))

    return ParsedSignal(
        action=action,
        symbol=m.group("symbol"),
        entry=entry,
        sl=sl,
        tps=tps,
        raw_message=text,
    )
