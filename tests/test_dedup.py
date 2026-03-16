"""
E3-2 — Tests de idempotencia y persistencia del DedupStore.
"""
import threading

import pytest

from src.store.dedup_store import DedupStore, Status


@pytest.fixture
def store(tmp_path):
    """DedupStore aislado por test usando directorio temporal de pytest."""
    with DedupStore(tmp_path / "dedup.db") as s:
        yield s


@pytest.fixture
def db_path(tmp_path):
    """Ruta de DB reutilizable entre instancias (test de persistencia)."""
    return tmp_path / "dedup.db"


# ===========================================================================
# mark_received — idempotencia
# ===========================================================================

class TestMarkReceived:
    def test_first_insert_returns_true(self, store):
        assert store.mark_received("sig-001") is True

    def test_duplicate_returns_false(self, store):
        store.mark_received("sig-001")
        assert store.mark_received("sig-001") is False

    def test_duplicate_called_many_times(self, store):
        store.mark_received("sig-001")
        for _ in range(10):
            assert store.mark_received("sig-001") is False

    def test_different_ids_are_independent(self, store):
        assert store.mark_received("sig-A") is True
        assert store.mark_received("sig-B") is True
        assert store.mark_received("sig-A") is False  # solo A es duplicado

    def test_initial_status_is_received(self, store):
        store.mark_received("sig-001")
        assert store.get_status("sig-001") == Status.RECEIVED


# ===========================================================================
# is_duplicate
# ===========================================================================

class TestIsDuplicate:
    def test_unknown_id_is_not_duplicate(self, store):
        assert store.is_duplicate("sig-999") is False

    def test_known_id_is_duplicate(self, store):
        store.mark_received("sig-001")
        assert store.is_duplicate("sig-001") is True

    def test_is_duplicate_does_not_modify_state(self, store):
        store.is_duplicate("sig-new")
        # Después de is_duplicate, mark_received debe seguir devolviendo True
        assert store.mark_received("sig-new") is True


# ===========================================================================
# update_status
# ===========================================================================

class TestUpdateStatus:
    def test_update_to_pending(self, store):
        store.mark_received("sig-001")
        store.update_status("sig-001", Status.PENDING)
        assert store.get_status("sig-001") == Status.PENDING

    def test_full_lifecycle(self, store):
        store.mark_received("sig-001")
        store.update_status("sig-001", Status.PENDING)
        store.update_status("sig-001", Status.EXECUTED)
        assert store.get_status("sig-001") == Status.EXECUTED

    def test_update_nonexistent_raises_key_error(self, store):
        with pytest.raises(KeyError, match="sig-ghost"):
            store.update_status("sig-ghost", Status.FAILED)

    def test_update_does_not_affect_other_signals(self, store):
        store.mark_received("sig-A")
        store.mark_received("sig-B")
        store.update_status("sig-A", Status.EXECUTED)
        assert store.get_status("sig-B") == Status.RECEIVED


# ===========================================================================
# Persistencia tras reinicio (E3-2 explícito)
# ===========================================================================

class TestPersistence:
    def test_signal_survives_close_and_reopen(self, db_path):
        with DedupStore(db_path) as store:
            store.mark_received("sig-persist")
            store.update_status("sig-persist", Status.PENDING)

        # Nueva instancia apuntando al mismo archivo
        with DedupStore(db_path) as store2:
            assert store2.is_duplicate("sig-persist") is True
            assert store2.get_status("sig-persist") == Status.PENDING

    def test_duplicate_check_persists_across_restart(self, db_path):
        with DedupStore(db_path) as store:
            store.mark_received("sig-001")

        with DedupStore(db_path) as store2:
            # No debe poder insertarse de nuevo
            assert store2.mark_received("sig-001") is False

    def test_new_id_accepted_after_restart(self, db_path):
        with DedupStore(db_path) as store:
            store.mark_received("sig-001")

        with DedupStore(db_path) as store2:
            # ID distinto debe insertarse sin problema
            assert store2.mark_received("sig-002") is True


# ===========================================================================
# Concurrencia — thread safety
# ===========================================================================

class TestConcurrency:
    def test_concurrent_mark_received_only_one_wins(self, store):
        """
        10 threads intentan insertar el mismo signal_id simultáneamente.
        Exactamente 1 debe tener éxito (True), los demás deben recibir False.
        """
        results = []
        lock = threading.Lock()

        def worker():
            result = store.mark_received("sig-concurrent")
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 9

    def test_concurrent_different_ids_all_succeed(self, store):
        """N threads con IDs distintos → todos deben tener éxito."""
        results = []
        lock = threading.Lock()

        def worker(n):
            result = store.mark_received(f"sig-{n}")
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results)
        assert len(results) == 20


# ===========================================================================
# Context manager
# ===========================================================================

class TestContextManager:
    def test_context_manager_closes_connection(self, tmp_path):
        with DedupStore(tmp_path / "cm.db") as store:
            store.mark_received("sig-001")
        # Después del with, la conexión debe estar cerrada
        with pytest.raises(Exception):
            store.mark_received("sig-002")

    def test_db_directory_created_automatically(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "dedup.db"
        with DedupStore(nested) as store:
            assert store.mark_received("sig-001") is True
