"""
Configuración de logging compartida.

stdout : ConsoleRenderer (legible para humanos, colores ANSI)
archivo: JSON con RotatingFileHandler (5 MB × 3 archivos)

Uso:
    from src.utils.logging_config import configure_logging
    configure_logging(Path("logs/server.log"))  # con archivo
    configure_logging()                          # solo stdout
"""
import json
import logging
import logging.handlers
from pathlib import Path

import structlog


def _make_file_sink(log_file: Path):
    """
    Devuelve un processor de structlog que escribe JSON a un RotatingFileHandler.
    Se inserta ANTES de ConsoleRenderer para que el event_dict esté completo.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    # Logger stdlib aislado: no propaga al root logger para evitar doble escritura
    stdlib_logger = logging.getLogger(f"_file_sink_{log_file.stem}")
    stdlib_logger.addHandler(handler)
    stdlib_logger.propagate = False
    stdlib_logger.setLevel(logging.DEBUG)

    def _processor(logger, method, event_dict):
        try:
            stdlib_logger.info(
                json.dumps(event_dict, default=str, ensure_ascii=False)
            )
        except Exception:
            pass
        return event_dict

    return _processor


def configure_logging(log_file: Path | None = None) -> None:
    """
    Configura structlog.

    - Siempre escribe a stdout con ConsoleRenderer.
    - Si log_file está definido, añade escritura JSON a archivo rotativo.
    """
    processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
    ]
    if log_file:
        processors.append(_make_file_sink(log_file))
    processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )
