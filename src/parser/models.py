from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class EntryType(str, Enum):
    MARKET = "MARKET"
    RANGE = "RANGE"
    LIMIT = "LIMIT"


@dataclass
class EntryPrice:
    type: EntryType
    price: float | None = None        # LIMIT — precio exacto
    range_low: float | None = None    # RANGE — extremo bajo
    range_high: float | None = None   # RANGE — extremo alto


@dataclass
class ParsedSignal:
    action: Action
    symbol: str
    entry: EntryPrice
    sl: float
    tps: list[float] = field(default_factory=list)
    raw_message: str = ""
    signal_id: str = ""        # SHA256(channel_id:message_id)
    source_channel: str = ""   # ID numérico del canal como string


class NoOpSignal:
    """Mensaje de actualización (move SL, TP hit, close…) — ignorar sin error."""
    def __init__(self, reason: str = "") -> None:
        self.reason = reason


class ParseError(Exception):
    """Formato inválido o ambiguo — no ejecutar."""


class ValidationError(Exception):
    """Precios incoherentes con la dirección de la operación — no ejecutar."""
