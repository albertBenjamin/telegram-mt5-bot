# Análisis histórico — GOLD VIP 2.0

**Canal:** `-1003224347994`
**Período:** 2025-11-03 → 2026-03-16 (4.5 meses)
**Generado con:** `scripts/analyze_history.py` + `scripts/analyze_outcomes.py`

---

## Resultados globales

| Métrica | Valor |
|---------|-------|
| Total señales | 237 |
| Win rate (TP1 o mejor) | **96.2%** (228 / 237) |
| SL hit | 8 (3.4%) |
| Canceladas | 1 (0.4%) |
| Sin resultado (UNKNOWN) | 0 |

### Desglose de wins

| Outcome | N | % |
|---------|---|---|
| TP3 alcanzado | 190 | 80.2% |
| TP2 alcanzado | 23 | 9.7% |
| TP1 alcanzado | 15 | 6.3% |
| **Total wins** | **228** | **96.2%** |

El grueso de las señales (80%) llega a TP3 — el canal mantiene sus posiciones hasta el objetivo máximo.

---

## Expectativa matemática

| Métrica | Pips |
|---------|------|
| Ganancia media (wins) | +8.2 |
| Pérdida media (SL hits) | +6.1 |
| **Expectativa por trade** | **+7.7** |

Con win rate del 96.2% y ratio ganancia/pérdida 8.2 / 6.1 ≈ 1.3:1,
el sistema tiene edge positivo sólido en el período analizado.

---

## Win rate mensual

| Mes | Win rate | Señales |
|-----|----------|---------|
| Nov 2025 | 98% | — |
| Dic 2025 | 98% | — |
| Ene 2026 | 98% | — |
| Feb 2026 | 97% | — |
| Mar 2026 | 87% | — |

La caída en marzo 2026 (87%) puede reflejar condiciones de mercado más
volátiles o que el mes no está completado. A monitorear.

---

## Distribución horaria

Horarios pico (UTC): **10:00 – 13:00**

Coincide con apertura de Londres + solapamiento con Nueva York.
La mayoría de señales se generan durante la sesión europea activa.

---

## Símbolo

100% XAUUSD — el canal opera exclusivamente oro.
El EA se carga en el gráfico XAUUSD-STD (VT Markets) y usa `Symbol()`
para ejecutar; el payload llega como "XAUUSD" y se usa solo para HMAC.

---

## Notas de calidad del análisis

- **0 señales UNKNOWN** — todo el historial tiene resultado clasificado.
  Antes de escalar lote, validar que el pattern matching no tenga falsos
  positivos (un TP/SL de señal A puede caer en la ventana de señal B si
  hay varias activas simultáneamente).

- El win rate histórico no garantiza resultados futuros. Las condiciones
  de marzo 2026 sugieren revisar semanalmente.

---

## Plan de escalado

| Fase | Condición | Lote |
|------|-----------|------|
| Actual | Monitoreo activo 2 semanas | 0.03 |
| Fase 2 | Win rate sostenido ≥ 90% en 2 semanas | 0.05 |
| Fase 3 | Revisar con equity real acumulado | TBD |

Criterio de pausa: activar kill switch si SL hit 3 veces consecutivas.

---

## Reproducir el análisis

```cmd
cd C:\bot\telegram-mt5-bot
.venv\Scripts\activate
python scripts/analyze_history.py    # genera data/gold_vip_history.csv
python scripts/analyze_outcomes.py   # genera data/gold_vip_outcomes.csv
```
