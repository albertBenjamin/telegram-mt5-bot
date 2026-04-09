"""
scripts/test_matchtrader_balance.py — Lee balance de cuenta MatchTrader (Actifunded).

Solo lectura. No abre posiciones.

Uso:
    python scripts/test_matchtrader_balance.py

Dependencias: requests (pip install requests)
"""

import json
import sys

import requests

# ── Credenciales ──────────────────────────────────────────────────────────────

BASE_URL   = "https://prop.actifunded.com"
EMAIL      = "albert.munozp@gmail.com"
PASSWORD   = "xnuqdz2thmbq"
BROKER_ID  = 215

# Session global — persiste cookies entre llamadas
SESSION = requests.Session()

# ─────────────────────────────────────────────────────────────────────────────

def pretty(data) -> str:
    try:
        return json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:
        return str(data)


def show(resp: requests.Response) -> None:
    print(f"  Status : {resp.status_code} {resp.reason}")
    try:
        print(pretty(resp.json()))
    except Exception:
        print(resp.text[:1000] or "(sin cuerpo)")


def get(url: str, *, headers: dict, params: dict | None = None,
        session: requests.Session | None = None) -> requests.Response:
    print(f"\n  → GET {url}")
    if params:
        print(f"    params: {params}")
    requester = session or requests
    resp = requester.get(url, headers=headers, params=params, timeout=15)
    show(resp)
    return resp


# ── PASO 1: Login → extraer AMBOS tokens y UUIDs dinámicos ───────────────────

def login() -> dict:
    """
    Devuelve:
      manager_token    — JWT root, necesario como Cookie en endpoints /match-trader-edge/
      trading_token    — tradingApiToken, para endpoints /mtr-api/ con Auth-trading-api
      account_uuid     — UUID de la cuenta de trading
      branch_uuid      — branchUuid extraído de la respuesta (no hardcodeado)
      system_uuid      — system.uuid del offer
    """
    url     = f"{BASE_URL}/manager/mtr-login"
    payload = {"email": EMAIL, "password": PASSWORD, "brokerId": BROKER_ID}

    print(f"\n{'='*60}")
    print("PASO 1 — Login")
    print(f"  POST {url}")
    print(f"  Body: {json.dumps({**payload, 'password': '***'})}")

    resp = SESSION.post(
        url, json=payload, timeout=15,
        headers={"Content-Type": "application/json"},
    )
    show(resp)
    resp.raise_for_status()

    data = resp.json()

    # Token manager (root del response) — usado como Cookie en back-office
    manager_token = data.get("token", "")

    # Token trading (anidado en selectedAccount) — usado con Auth-trading-api
    selected = data.get("selectedAccount") or (data.get("accounts") or [{}])[0]
    trading_token = selected.get("tradingApiToken", "")
    account_uuid  = selected.get("uuid", "")
    branch_uuid   = selected.get("branchUuid", "")
    system_uuid   = (selected.get("offer") or {}).get("system", {}).get("uuid", "")

    print(f"\n  manager_token  : {manager_token[:50]}...")
    print(f"  trading_token  : {trading_token[:50]}...")
    print(f"  account_uuid   : {account_uuid}")
    print(f"  branch_uuid    : {branch_uuid}")
    print(f"  system_uuid    : {system_uuid}")

    print(f"\n  Cookies recibidas del login:")
    if SESSION.cookies:
        for c in SESSION.cookies:
            print(f"    {c.name} = {c.value[:60]}{'...' if len(c.value) > 60 else ''}"
                  f"  (domain={c.domain}, path={c.path})")
    else:
        print("    (ninguna — el servidor no estableció cookies)")

    return {
        "manager_token": manager_token,
        "trading_token": trading_token,
        "account_uuid":  account_uuid,
        "branch_uuid":   branch_uuid,
        "system_uuid":   system_uuid,
    }


# ── PASO 2: finance-history con Session + headers adicionales ────────────────

def probe_finance_with_session(ctx: dict) -> None:
    """
    Prueba el endpoint de finance-history de tres formas:
      A) Session pura (cookies del login, sin header extra)
      B) Session + Auth-token: {manager_token}
      C) Session + X-Auth-Token: {manager_token}
    También intenta con trading_token en los mismos headers.
    Usa el branchUuid observado en DevTools (974e31f5) además del dinámico.
    """
    print(f"\n{'='*60}")
    print("PASO 2 — finance-history con Session (cookies automáticas)")

    url = f"{BASE_URL}/match-trader-edge/finance-history/paid-payment-gateways"

    # Probar ambos branchUuid: el de DevTools y el dinámico del login
    branch_uuids = {
        "devtools":  "974e31f5-3240-40c3-bb8a-ae4617e60889",
        "login":     ctx["branch_uuid"],
    }

    for buuid_label, buuid in branch_uuids.items():
        params = {
            "tradingAccountUuid": ctx["account_uuid"],
            "branchUuid":         buuid,
            "currency":           "USD",
        }
        print(f"\n  branchUuid [{buuid_label}]: {buuid}")

        token_variants = {
            "manager": ctx["manager_token"],
            "trading": ctx["trading_token"],
        }

        for tok_label, tok in token_variants.items():
            header_variants = [
                ("Session sola (sin header extra)",   {}),
                (f"Auth-token [{tok_label}]",          {"Auth-token":   tok}),
                (f"X-Auth-Token [{tok_label}]",        {"X-Auth-Token": tok}),
                (f"Auth-trading-api [{tok_label}]",    {"Auth-trading-api": tok}),
                (f"Authorization Bearer [{tok_label}]",{"Authorization": f"Bearer {tok}"}),
            ]

            for label, extra_hdrs in header_variants:
                hdrs = {"Accept": "application/json", **extra_hdrs}
                resp = SESSION.get(url, headers=hdrs, params=params, timeout=15)
                print(f"    [{label}] → {resp.status_code}")
                if resp.status_code == 200:
                    show(resp)
                    print(f"\n  ✓ ÉXITO — branchUuid={buuid_label}, token={tok_label}, header={label}")
                    return
                # Solo imprimir body en errores distintos de 401/403 para no saturar
                if resp.status_code not in (401, 403):
                    try:
                        print(f"      {resp.json()}")
                    except Exception:
                        print(f"      {resp.text[:200]}")

    print("\n  ✗ finance-history no respondió 200 con ninguna combinación.")


# ── PASO 3: Endpoints back-office (/match-trader-edge/) ──────────────────────
# Estos requieren el manager_token — probar como Cookie Y como Bearer.

def probe_backoffice(ctx: dict) -> None:
    print(f"\n{'='*60}")
    print("PASO 3 — Endpoints back-office (/match-trader-edge/)")

    mtoken = ctx["manager_token"]
    auuid  = ctx["account_uuid"]
    buuid  = ctx["branch_uuid"]

    endpoints = [
        # finance-history con branchUuid DINÁMICO (del login, no hardcodeado)
        (
            f"{BASE_URL}/match-trader-edge/finance-history/paid-payment-gateways",
            {"tradingAccountUuid": auuid, "branchUuid": buuid, "currency": "USD"},
        ),
        (
            f"{BASE_URL}/match-trader-edge/trading-accounts/{auuid}",
            None,
        ),
        (
            f"{BASE_URL}/match-trader-edge/trading-accounts/{auuid}/balance",
            None,
        ),
        (
            f"{BASE_URL}/match-trader-edge/accounts/{auuid}",
            None,
        ),
    ]

    # manager_token como Cookie es el patrón estándar de MatchTrader back-office
    header_variants = [
        {"Cookie": f"token={mtoken}", "Accept": "application/json"},
        {"Authorization": f"Bearer {mtoken}", "Accept": "application/json"},
        {"Auth-trading-api": ctx["trading_token"], "Accept": "application/json"},
    ]

    for url, params in endpoints:
        print(f"\n  URL: {url}")
        for hdrs in header_variants:
            auth_hint = next((f"{k}: {v[:30]}..." for k, v in hdrs.items()
                              if k in ("Cookie", "Authorization", "Auth-trading-api")), "")
            resp = requests.get(url, headers=hdrs, params=params, timeout=15)
            print(f"    [{auth_hint}] → {resp.status_code}")
            if resp.status_code == 200:
                show(resp)
                print(f"  ✓ ÉXITO — guardar esta combinación para monitor_dd.py")
                return
            if resp.status_code not in (401, 403):
                break   # 404/500 → mismo header no servirá en otras variantes


# ── PASO 3: Endpoints trading API (/mtr-api/) ────────────────────────────────
# Estos requieren tradingApiToken en Auth-trading-api.
# Según el login, tradingApiDomain interno es http://ta-qfx-mtr:8080
# El proxy público expone /mtr-api/{systemUuid}/

def probe_trading_api(ctx: dict) -> None:
    print(f"\n{'='*60}")
    print("PASO 3 — Endpoints trading API (/mtr-api/)")

    ttoken = ctx["trading_token"]
    suuid  = ctx["system_uuid"]
    auuid  = ctx["account_uuid"]

    mtr_base = f"{BASE_URL}/mtr-api/{suuid}"

    # Probar paths sin UUID en la URL (la cuenta se identifica por el token)
    endpoints = [
        f"{mtr_base}/account",
        f"{mtr_base}/balance",
        f"{mtr_base}/accounts",
        f"{mtr_base}/trading-account",
        # Con UUID explícito
        f"{mtr_base}/account/{auuid}",
        f"{mtr_base}/balance/{auuid}",
    ]

    hdrs = {"Auth-trading-api": ttoken, "Accept": "application/json"}

    for url in endpoints:
        resp = get(url, headers=hdrs, session=SESSION)
        if resp.status_code == 200:
            print(f"  ✓ ÉXITO — endpoint de balance confirmado")
            return


# ── PASO 4: Probar si existe Swagger/OpenAPI ─────────────────────────────────

def probe_swagger(ctx: dict) -> None:
    print(f"\n{'='*60}")
    print("PASO 4 — Buscar documentación de la API (Swagger/OpenAPI)")

    suuid = ctx["system_uuid"]
    paths = [
        f"{BASE_URL}/match-trader-edge/swagger-ui/index.html",
        f"{BASE_URL}/match-trader-edge/v3/api-docs",
        f"{BASE_URL}/match-trader-edge/swagger-ui.html",
        f"{BASE_URL}/mtr-api/{suuid}/swagger-ui/index.html",
        f"{BASE_URL}/mtr-api/{suuid}/v3/api-docs",
    ]
    hdrs = {"Accept": "application/json, text/html"}
    for url in paths:
        resp = requests.get(url, headers=hdrs, timeout=10)
        print(f"  {resp.status_code}  {url}")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ctx = login()
    probe_finance_with_session(ctx)
    probe_backoffice(ctx)
    probe_trading_api(ctx)
    probe_swagger(ctx)

    print(f"\n{'='*60}")
    print("Fin de la prueba.")
