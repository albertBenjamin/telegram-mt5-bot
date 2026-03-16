"""
E3-1 — DedupStore: almacén de idempotencia basado en SQLite.

Garantías:
- mark_received() es atómico via INSERT OR IGNORE → seguro ante condiciones de carrera
- threading.Lock protege acceso concurrente (FastAPI + listener en threads distintos)
- El estado persiste entre reinicios del proceso
"""
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


# Valores de status permitidos
class Status:
    RECEIVED  = "received"   # señal recibida por el server
    PENDING   = "pending"    # en cola, esperando al EA
    EXECUTED  = "executed"   # EA confirmó la ejecución
    FAILED    = "failed"     # error en la ejecución


_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id   TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    received_at TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class DedupStore:
    """
    Almacén SQLite de señales procesadas.

    Uso típico:
        store = DedupStore("data/dedup.db")
        if not store.mark_received(signal_id):
            return  # duplicado, ignorar

    Uso como context manager:
        with DedupStore("data/dedup.db") as store:
            ...
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,  # protegido por _lock
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")  # mejor concurrencia
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def is_duplicate(self, signal_id: str) -> bool:
        """Devuelve True si signal_id ya existe en la base de datos."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM signals WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            return row is not None

    def mark_received(self, signal_id: str) -> bool:
        """
        Intenta insertar signal_id con status 'received'.

        Returns:
            True  → recién insertado (no era duplicado).
            False → ya existía (duplicado, ignorar).
        """
        now = _now_utc()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO signals (signal_id, status, received_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (signal_id, Status.RECEIVED, now, now),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def update_status(self, signal_id: str, status: str) -> None:
        """
        Actualiza el status de una señal existente.

        Raises:
            KeyError: si signal_id no existe en la base de datos.
        """
        now = _now_utc()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE signals SET status = ?, updated_at = ? WHERE signal_id = ?",
                (status, now, signal_id),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"signal_id not found: {signal_id!r}")

    def get_status(self, signal_id: str) -> str | None:
        """Devuelve el status actual o None si no existe."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM signals WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "DedupStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()
