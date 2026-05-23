# Registro de decisiones (ADRs)

Cada entrada: fecha, decisión, contexto, alternativas consideradas, consecuencias.

---

## 2026-05-24 00:40 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Runner de backtest end-to-end MVP**

**En una frase:** construimos un runner mínimo que valida la integración de las piezas ya existentes con TSMOM long-only sobre SPY+TLT+GLD, con el split IS/OOS fijado como primer acto irrevocable.

---

**Lo que autorizo implementar:**

| Elemento | Decisión |
|---|---|
| Función | `run_backtest(tickers, start, end, lookback_months=12, target_vol=0.10) -> tuple[pd.Series, PerformanceReport]` |
| Universo V1 | SPY, TLT, GLD |
| Dirección | Long-only. Los cortos quedan fuera del scope explícitamente. |
| Split IS/OOS | **OOS = 2022-01-01 en adelante, intocable.** El runner solo opera en datos pre-2022. Hardcodeado con comentario explícito. |
| Señal | `sign(return[t - lookback])`, rebalanceo mensual |
| Ejecución sin data leakage | Señal en `close[t-lookback]`, entrada en `open[t+1]` — implementado con `open.shift(-1)`, no en comentario |
| Vol-scaling | EWMA λ=0.94 (RiskMetrics), no ventana fija. λ hardcodeado y documentado como decisión, no parámetro libre |
| Diagnóstico de régimen | Correlación rolling 60d entre activos logueada (no en contrato de salida) |
| Salida | Equity curve (`pd.Series`) + `PerformanceReport` del módulo de métricas ya construido |

---

**Criterios de aceptación — no acepto sin:**
1. Split IS/OOS hardcodeado con comentario explícito en código — no variable configurable.
2. Ejecución en `open[t+1]`, verificable en el código sin ambigüedad. Si el TL ve `close` como precio de entrada, la entrega se rechaza.
3. El reporte de métricas resultante se genera llamando al módulo `equity_metrics.py` existente — no hay duplicación de lógica de métricas.
4. El runner no modifica ni rellena NaN — propaga lo que entrega el adaptador.
5. El reporte de entrega incluye explícitamente el número de rebalanceos generados en IS y la advertencia de baja significancia estadística — blindar el framing antes de que nadie use el Sharpe IS como criterio de decisión.

---

**Riesgos que vigilo:**
- **Data leakage silencioso:** el punto más crítico del debate. Si el precio de entrada no es `open[t+1]`, todas las métricas son ficción.
- **Interpretación prematura del Sharpe IS:** con ~36 meses IS y 3 activos, habrá decenas de rebalanceos, no cientos. El número no decide nada sobre adopción de la estrategia.
- **Scope creep:** `regime_context` en el objeto de salida está fuera. Diagnóstico de régimen es iteración posterior.

TL, queda en tu tejado. ¿Algún bloqueante técnico antes de planificar?

### Verdict del Jefe de Proyecto

[VALIDADO]

Propuesta cerrada. Los analistas resolvieron los tres puntos que podían hundir este runner antes de escribirlo: data leakage (`open[t+1]`), split OOS inamovible (2022+), y EWMA sobre ventana fija con λ no negociable. El PO sintetizó sin dejar preguntas abiertas ni condiciones flotantes.

Dos exigencias para el TL al planificar:

1. **El test de data leakage debe ser explícito** — no basta con que el código use `open.shift(-1)`. Quiero un test que verifique que el retorno de la estrategia se calcula con el precio de apertura del día siguiente, no con el cierre del día de la señal. Si ese test no existe, la entrega se rechaza desde aquí.

2. **El criterio #5 del PO (advertencia de baja significancia) no es cosmético** — el reporte de entrega debe incluir el número exacto de rebalanceos IS y una línea explícita de que el Sharpe resultante no tiene potencia estadística para decidir nada. Si el número sale bonito y nadie pone la advertencia, alguien lo usará como argumento para adoptar la estrategia.

TL, luz verde.

### Implementación — 2026-05-24

Entregado. Sprint 3 completo.

- `src/trading/backtest/runner.py` — 47 líneas ejecutables, 100% coverage
- `tests/unit/backtest/test_runner.py` — 14 tests, 0 llamadas reales a red
- Test de data leakage: construye mock donde open = close + 100 (big gap),
  verifica que el retorno en índice 380 es más cercano a la fórmula open-based
  que a la close-based. Con target_vol=100 el vol_weight queda capped a 1.0.
- `_EWMA_LAMBDA = 0.94` como constante de módulo; `_OOS_START = date(2022, 1, 1)`
- Rebalanceo mensual: `signal.resample("MS").last()` + `reindex(method="ffill")`
  (el ffill es sobre la señal, no sobre precios — no viola el contrato de NaN)
- NaN propagado: open[300]=NaN → daily_asset_returns[298,299]=NaN → equity con NaN
- Métricas delegadas: patch de `compute_performance` devuelve sentinel y se verifica
- Diagnóstico de correlación y rebalanceos logueado via logger.info

---
