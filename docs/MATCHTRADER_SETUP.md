# MATCHTRADER COPIER — Guía de Setup

**Objetivo**: Replicar trades desde MT5 (VTMarkets, cuenta #24430609) hacia MatchTrader (Actifunded, cuenta #808852, Fast-Track 100k).

**Restricción crítica**: No tocar `TelegramSignalEA.mq5` ni los servicios NSSM existentes.

---

## 1. Comparativa de Copiers

### Opción A — MT5 to MatchTrader Copier (MeetAlgo)

| Aspecto | Detalle |
|---|---|
| Mecanismo | EA (Expert Advisor) que corre en MT5, usa WebRequest |
| Precio | Demo gratis (15 min/día) · Pro **$45 USD** pago único |
| Soporte | MT4 + MT5 → MatchTrader |
| Símbolo mapping | Manual: `XAUUSD-STD=XAUUSD` en parámetros del EA |
| Filtros | Por magic number, símbolo, tipo de orden, lote mínimo/máximo |
| Velocidad | Modo "FIRSTER" (tick-based, recomendado) |
| URL oficial | https://meetalgo.com/product/mt4-mt5-to-matchtrader-copier/ |

### Opción B — MetaTrader to MatchTrader (DaneTrades)

| Aspecto | Detalle |
|---|---|
| Mecanismo | EA en MT5, usa WebRequest |
| Precio | Demo 7 días · Multi License **$25/mes** (todos los copiers) |
| Soporte | MT4/5 → MatchTrader, DXTrade, TradeLocker, cTrader |
| Símbolo mapping | Manual + relative pricing (ajusta spreads automáticamente) |
| Filtros | Magic number, lotes, Max Daily Loss integrado |
| Ventaja clave | Relative pricing compensa diferencias de spread entre brokers |
| URL oficial | https://danetrades.com/metatrader-to-matchtrader/ |

### Recomendación: **DaneTrades** (Opción B)

**Justificación**:
1. **Relative pricing**: Actifunded puede tener spread distinto a VTMarkets en XAUUSD — DaneTrades ajusta automáticamente el precio de entrada; MeetAlgo copia el precio exacto (puede rechazar si difiere demasiado).
2. **Max Daily Loss integrado**: Tiene kill-switch por DD diario como parámetro nativo del EA, alineado con las reglas de la prop firm.
3. **Precio**: $25/mes Multi License es más económico que $45 pago único si se necesita ajustar o cambiar en el futuro.
4. **Trial 7 días**: Permite validar compatibilidad con Actifunded antes de pagar.

---

## 2. Instalación — DaneTrades MT5 to MatchTrader

### Paso 1: Descargar el EA

1. Crear cuenta en https://danetrades.com
2. Descargar el EA `.ex5` desde el panel de cliente
3. Durante el trial de 7 días **no se requiere licencia activa**

### Paso 2: Instalar el EA en MT5 (VPS)

```
# En el VPS, abrir MT5 (cuenta #24430609)
# File → Open Data Folder → MQL5 → Experts
# Copiar el archivo .ex5 descargado a esa carpeta
# Reiniciar MT5 (o F4 → Actualizar)
```

### Paso 3: Permitir WebRequest para MatchTrader

```
MT5 → Tools → Options → Expert Advisors
✅ Allow WebRequest for listed URL:
    https://prop.actifunded.com
```

> El formato debe ser solo el dominio base, sin paths ni trailing slash variante — verificar en la documentación de DaneTrades qué formato exacto aceptan.

### Paso 4: Cargar el EA en un gráfico

1. Abrir un **nuevo gráfico vacío** (ej. EURUSD M1) — **NO** el gráfico donde corre `TelegramSignalEA.mq5`
2. Arrastrar el EA `DaneTrades_MT5toMatchTrader` al gráfico
3. Configurar parámetros (ver sección 3)

---

## 3. Configuración del EA

### Credenciales MatchTrader

```
Server URL     : https://prop.actifunded.com
Server Name    : Match-Trader
Email          : albert.munozp@gmail.com
Account Number : 808852
```

> Las credenciales van **solo** en los parámetros del EA en MT5 — no en archivos de texto ni en repositorios.

### Parámetros de operación

```
Magic Number         : 99999        ← número único, diferente al de TelegramSignalEA
Copy All Magic Nos   : false        ← solo copiar órdenes del TelegramSignalEA
Source Magic Number  : <magic del TelegramSignalEA>   ← ver abajo cómo obtenerlo
Lot Multiplier       : ver sección 4
Relative Pricing     : true         ← recomendado para compensar spreads
Max Daily Loss       : 2400         ← USD (ver sección 4 — dejar margen sobre 2500)
Max Daily Loss Mode  : CloseTrades  ← cerrar posiciones abiertas al alcanzar límite
```

> **Cómo obtener el Magic Number de TelegramSignalEA**: En MT5 → Terminal → Trade → columna "Comment" — cada orden tiene el comment del EA. El magic number está en `MqlTradeRequest.magic`. Alternativamente, abrir `ea/TelegramSignalEA.mq5` y buscar `InpMagic` o el valor hardcodeado.

---

## 4. Mapeo de Símbolos

| MT5 VTMarkets (origen) | MatchTrader Actifunded (destino) |
|---|---|
| `XAUUSD-STD` | `XAUUSD` |
| `DJ30.s` | `US30` |
| `NAS100.s` | `US100` |
| `SP500.s` | `US500` |
| `ES35.s` | `GER40` |
| `UK100.s` | `UK100` |

Configurar en el EA (un par por línea):
```
Symbol Map:
  XAUUSD-STD=XAUUSD
  DJ30.s=US30
  NAS100.s=US100
  SP500.s=US500
  ES35.s=GER40
  UK100.s=UK100
```

---

## 5. Cálculo del Lot Multiplier

### Límites de la prop firm (Fast-Track 100k)

| Regla | Valor |
|---|---|
| Max Daily Loss | 2.5% = **$2,500** |
| Max Total Drawdown | 5% = **$5,000** |

### Fórmula

```
Multiplier = (Prop_Account_Size / MT5_Account_Balance) × Risk_Scaling_Factor
```

### Estimación conservadora

El EA actual ejecuta **0.03 lotes por TP**, 3 órdenes por señal = 0.09 lotes totales.

Para XAUUSD-STD (100 oz/lote):
- SL típico de una señal: ~$4-8 por punto (ej. señal `5181-5185 / SL 5189` → 4-8 pts)
- Pérdida máxima por orden de 0.03 lotes con SL de 8 pts: `0.03 × 100 × 8 = $24`
- Pérdida máxima por señal completa (3 órdenes): `$72`
- Para consumir el 50% del DD diario ($1,250) necesitarían ~17 señales perdedoras consecutivas

**Recomendación**: empezar con `Lot Multiplier = 1.0` (copia exacta) y monitorear. Si el balance de MT5 es significativamente menor a $100k, escalar hacia arriba con precaución.

**Multiplicadores de referencia**:
| MT5 Balance | Multiplier sugerido | Lotes en prop (por orden) |
|---|---|---|
| $1,000 | 5.0 | 0.15 |
| $3,000 | 2.0 | 0.06 |
| $5,000 | 1.0 | 0.03 |
| $10,000+ | 0.5 | 0.015 |

> Ajustar `Max Daily Loss` en el EA a **$2,400** (no $2,500) para dejar margen antes del límite real de la prop.

---

## 6. Parámetros Críticos de Protección

```
Max Daily Loss       : 2400 USD     ← 0.1% de margen sobre límite de prop ($2,500)
Max Daily Loss Mode  : CloseTrades  ← cierra posiciones + detiene nuevas
Relative Pricing     : true         ← ajusta slippage entre brokers
Copy Pending Orders  : true         ← el EA usa SELL_LIMIT/BUY_LIMIT
Copy SL/TP          : true
Close on Opposite   : false         ← no cerrar si llega señal contraria (gestión manual)
```

---

## 7. Plan de Testing (Antes de Activar en Real)

### Fase 1 — Demo MatchTrader (3 días mínimo)

- [ ] Abrir cuenta demo en Actifunded (o usar la cuenta de práctica si la plataforma la ofrece)
- [ ] Instalar EA en gráfico separado del VPS
- [ ] Verificar que `TelegramSignalEA.mq5` sigue operando sin interferencia (revisar logs)
- [ ] Enviar señal de prueba al bot → confirmar que aparece en MT5 **Y** en MatchTrader
- [ ] Verificar símbolo correcto (`XAUUSD` en MatchTrader, no rechazado)
- [ ] Verificar que SL y TP se copian correctamente
- [ ] Verificar que los 3 tickets (una orden por TP) se replican como 3 órdenes en MatchTrader
- [ ] Confirmar que el comment/magic del EA origen se filtra correctamente

### Fase 2 — Cuenta real Actifunded (primera semana)

- [ ] Activar con `Lot Multiplier = 0.5` los primeros 2 días
- [ ] Monitorear equity diariamente con `monitor_dd.py`
- [ ] Verificar alertas Telegram funcionan correctamente
- [ ] Si todo OK, subir a `Lot Multiplier = 1.0`

### Checklist pre-activación

```
[ ] TelegramSignalEA.mq5 no modificado — verificar con git diff
[ ] Nuevo EA corre en gráfico distinto (no interfiere con el existente)
[ ] WebRequest habilitado para https://prop.actifunded.com en MT5
[ ] Symbol mapping configurado (XAUUSD-STD=XAUUSD, DJ30.s=US30, NAS100.s=US100, SP500.s=US500, ES35.s=GER40, UK100.s=UK100)
[ ] Magic Number correcto del EA origen
[ ] Max Daily Loss = 2400 (no más)
[ ] monitor_dd.py corriendo y alertas verificadas
[ ] Backup de dedup.db antes de activar (scripts/backup_db.bat)
```

---

## 8. Servicio NSSM para el Copier EA

El copier **corre dentro de MT5** como EA — no necesita servicio NSSM independiente. MT5 ya está como proceso persistente en el VPS.

Si se necesita reinicio automático de MT5:
```bat
# En VPS (no ejecutar ahora — documentar para el futuro)
C:\tools\nssm.exe install mt5-instance "C:\path\to\terminal64.exe"
C:\tools\nssm.exe set mt5-instance AppRestartDelay 5000
```

---

## 9. Troubleshooting Rápido

| Síntoma | Causa probable | Solución |
|---|---|---|
| EA no copia → error WebRequest | URL no en whitelist | Tools → Options → EA → agregar URL |
| Orden rechazada en MatchTrader | Símbolo no encontrado | Verificar nombre exacto en Market Watch de Actifunded |
| Lots incorrectos | Multiplier mal calibrado | Ajustar `Lot Multiplier` en parámetros EA |
| DD alcanzado, EA paró | Límite diario activado | Revisar trades, reiniciar EA mañana |
| TelegramSignalEA se detuvo | Conflicto de recursos | Verificar que cada EA corre en gráfico separado |

---

## 10. Pasos Inmediatos

1. Comprar EA en https://danetrades.com/metatrader-to-matchtrader/
2. Copiar el `.ex5` descargado al VPS: `C:\Program Files\MetaTrader 5\MQL5\Experts\`
3. En MT5 → Tools → Options → Expert Advisors → agregar `https://prop.actifunded.com` a la whitelist de WebRequest
4. Abrir un gráfico nuevo (ej. EURUSD M1) — separado del gráfico donde corre `TelegramSignalEA.mq5`
5. Cargar el EA en ese gráfico y configurar con las credenciales de la sección 3
6. Activar los límites de protección:
   - Max Daily Loss: **$2,400** (equivale a 2.4% — margen antes del límite de la prop de 2.5%)
   - Max Total DD: configurar stop si equity cae **$4,800** desde balance máximo (margen antes del 5%)

---

*Última actualización: 2026-04-08*
*Copier recomendado: DaneTrades Multi License ($25/mes)*
*Cuentas: MT5 VTMarkets #24430609 → MatchTrader Actifunded #808852*
