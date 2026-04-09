"""
scripts/test_matchtrader_api.py — Prueba de conexión a la API REST de MatchTrader.

Solo lectura: login + balance. NO abre posiciones.

Uso:
    python scripts/test_matchtrader_api.py

Dependencias: requests (pip install requests)
"""

import json
import sys

import requests

# ── Credenciales (solo para prueba — no commitear con valores reales) ─────────

BASE_URL   = "https://prop.actifunded.com"
PARTNER_ID = 215
EMAIL      = "albert.munozp@gmail.com"
PASSWORD   = "xnuqdz2thmbq"
ACCOUNT_ID = "808852"

HEADERS_JSON = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ─────────────────────────────────────────────────────────────────────────────

def pretty(data) -> str:
    try:
        return json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:
        return str(data)


def try_request(method: str, url: str, **kwargs) -> requests.Response | None:
    print(f"\n  → {method.upper()} {url}")
    try:
        resp = requests.request(method, url, timeout=15, **kwargs)
        print(f"  ← {resp.status_code} {resp.reason}")
        try:
            body = resp.json()
            print(pretty(body))
        except Exception:
            print(resp.text[:500] or "(sin cuerpo)")
        return resp
    except requests.RequestException as exc:
        print(f"  ✗ Error de red: {exc}")
        return None


# ── PASO 1: Login ─────────────────────────────────────────────────────────────

def step_login() -> str | None:
    print("\n" + "=" * 60)
    print("PASO 1 — Login (obtener token)")
    print("=" * 60)

    # Variantes de endpoint y payload observadas en distintas versiones de MatchTrader
    candidates = [
        # (url, payload)
        (
            f"{BASE_URL}/match-trader-edge/api/login",
            {"email": EMAIL, "password": PASSWORD, "partnerId": PARTNER_ID},
        ),
        (
            f"{BASE_URL}/match-trader-edge/multi-broker-access/available-brokers/login",
            {"email": EMAIL, "password": PASSWORD, "partnerId": PARTNER_ID},
        ),
        (
            f"{BASE_URL}/match-trader-edge/api/v1/login",
            {"email": EMAIL, "password": PASSWORD, "partnerId": PARTNER_ID},
        ),
        (
            f"{BASE_URL}/manager/mtr-login",
            {"email": EMAIL, "password": PASSWORD, "brokerId": str(PARTNER_ID)},
        ),
        (
            f"{BASE_URL}/match-trader-edge/api/auth/login",
            {"email": EMAIL, "password": PASSWORD, "partnerId": PARTNER_ID},
        ),
    ]

    for url, payload in candidates:
        resp = try_request("POST", url, headers=HEADERS_JSON, json=payload)
        if resp is None:
            continue
        if resp.status_code in (200, 201):
            token = _extract_token(resp)
            if token:
                print(f"\n  ✓ Token obtenido: {token[:40]}...")
                return token
            else:
                print("  ⚠ Respuesta 200 pero no se encontró token — revisar campos arriba")
                return None
        # 404 → probar siguiente; otros errores → parar en ese endpoint e informar
        if resp.status_code not in (404, 405):
            print(f"  ⚠ Código inesperado {resp.status_code} — revisar respuesta arriba")

    print("\n  ✗ Ningún endpoint de login respondió con éxito.")
    return None


def _extract_token(resp: requests.Response) -> str | None:
    """Busca el token en los campos más comunes de la respuesta."""
    try:
        data = resp.json()
    except Exception:
        return None

    # Campos directos conocidos
    for key in ("tradingApiToken", "token", "accessToken", "access_token",
                "authToken", "jwtToken", "sessionToken"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key]

    # Un nivel de anidamiento (ej. {"data": {"token": "..."}})
    for val in data.values():
        if isinstance(val, dict):
            for key in ("tradingApiToken", "token", "accessToken", "access_token"):
                if isinstance(val.get(key), str) and val[key]:
                    return val[key]

    return None


# ── PASO 2: Balance ───────────────────────────────────────────────────────────

def step_balance(token: str) -> None:
    print("\n" + "=" * 60)
    print("PASO 2 — Balance de cuenta")
    print("=" * 60)

    # MatchTrader acepta el token en distintos headers según versión
    auth_headers_variants = [
        {**HEADERS_JSON, "Auth-trading-api": token},
        {**HEADERS_JSON, "Authorization": f"Bearer {token}"},
        {**HEADERS_JSON, "Authorization": token},
        {**HEADERS_JSON, "X-Auth-Token": token},
    ]

    endpoints = [
        f"{BASE_URL}/match-trader-edge/api/balance",
        f"{BASE_URL}/match-trader-edge/api/account/{ACCOUNT_ID}/balance",
        f"{BASE_URL}/match-trader-edge/api/accounts/{ACCOUNT_ID}",
        f"{BASE_URL}/match-trader-edge/api/v1/balance",
        f"{BASE_URL}/rest-api/account/{ACCOUNT_ID}",
        f"{BASE_URL}/match-trader-edge/api/trading/account",
    ]

    for url in endpoints:
        for hdrs in auth_headers_variants:
            resp = try_request("GET", url, headers=hdrs)
            if resp is None:
                break   # error de red en esta URL → saltar a la siguiente
            if resp.status_code == 200:
                print(f"\n  ✓ Balance obtenido con header: {_auth_header_name(hdrs)}")
                return
            if resp.status_code == 401:
                # Token rechazado con este header → probar siguiente header
                continue
            if resp.status_code == 404:
                break   # endpoint no existe → probar siguiente URL
            # Otro código → informar y seguir
            break

    print("\n  ✗ No se pudo obtener balance con ninguna combinación de endpoint/header.")
    print("  Sugerencia: inspeccionar el tráfico de red en DevTools mientras navegas")
    print("  en https://prop.actifunded.com y buscar llamadas /balance o /account.")


def _auth_header_name(hdrs: dict) -> str:
    for k in ("Auth-trading-api", "Authorization", "X-Auth-Token"):
        if k in hdrs:
            return f"{k}: {hdrs[k][:30]}..."
    return "desconocido"


# ── EXTRA: Listar endpoints disponibles ──────────────────────────────────────

def step_probe_root() -> None:
    print("\n" + "=" * 60)
    print("EXTRA — Sondeo de rutas raíz (sin autenticación)")
    print("=" * 60)

    probes = [
        f"{BASE_URL}/match-trader-edge/",
        f"{BASE_URL}/match-trader-edge/api/",
        f"{BASE_URL}/match-trader-edge/swagger-ui/index.html",
        f"{BASE_URL}/match-trader-edge/v3/api-docs",
        f"{BASE_URL}/match-trader-edge/api/v1/",
    ]
    for url in probes:
        try_request("GET", url, headers={"Accept": "application/json, text/html"})


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("MatchTrader API — Prueba de conexión")
    print(f"Base URL   : {BASE_URL}")
    print(f"Partner ID : {PARTNER_ID}")
    print(f"Email      : {EMAIL}")
    print(f"Account    : {ACCOUNT_ID}")

    step_probe_root()

    token = step_login()
    if token:
        step_balance(token)
    else:
        print("\n⚠ Sin token — omitiendo paso de balance.")
        print("Revisar los endpoints probados arriba para identificar el correcto.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Fin de la prueba.")
