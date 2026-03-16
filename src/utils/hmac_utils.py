"""
HMAC-SHA256 para firmar y verificar señales.

El listener firma antes de enviar al server.
El server verifica al recibir.

Canonical string: JSON con claves ordenadas, sin espacios,
excluyendo el campo 'hmac_sha256' para evitar circularidad.
"""
import hashlib
import hmac
import json


def _canonical(payload: dict) -> bytes:
    """Serialización determinista: JSON compacto, claves ordenadas, sin hmac_sha256."""
    data = {k: v for k, v in payload.items() if k != "hmac_sha256"}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def sign(payload: dict, secret: str) -> str:
    """Calcula HMAC-SHA256 del payload y devuelve el hex digest."""
    return hmac.new(secret.encode(), _canonical(payload), hashlib.sha256).hexdigest()


def verify(payload: dict, secret: str) -> bool:
    """
    Verifica el campo hmac_sha256 del payload contra el secret.
    Usa compare_digest para evitar timing attacks.
    """
    expected = sign(payload, secret)
    received = payload.get("hmac_sha256", "")
    return hmac.compare_digest(expected, received)
