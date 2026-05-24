# Registro de decisiones (ADRs)

Cada entrada: fecha, decisión, contexto, alternativas consideradas, consecuencias.

---

## 2026-05-24 — Sprint 4: IS runner script + atribución

### Decisiones de diseño
- `compute_asset_attribution` importa helpers privados de runner (`_compute_monthly_signal`, `_compute_ewma_vol_weight`, `_pivot_ohlcv`) en lugar de duplicar lógica. Duplicación < 20 líneas → no se extrajo a `src/trading/analysis/`.
- `compute_subperiod_sharpes` delega en `compute_performance` para Sharpe sub-período (mismo ddof=1, mismo factor 252).
- Columnas del DataFrame de atribución ordenadas alfabéticamente por pandas `unstack` (no por orden del parámetro `tickers`). Test ajustado a `set` comparison.
- `_make_metrics` convierte NaN a `None` (JSON `null`) vía `_fmt()` helper.
- Root `conftest.py` añade `scripts/` a sys.path para que tests importen `run_is`.
- Warmup: `equity_curve.iloc[lookback_months * 21:]` — structural, no fecha fija.

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

## 2026-05-24 01:34 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Ejecución IS y análisis de output**

**En una frase:** ejecutamos el runner contra datos reales IS (2005–2021) con un script de análisis que produce equity curve, drawdown, desglose por sub-período y atribución por activo — todo sin tocar el contrato del runner ya validado.

---

**Lo que autorizo implementar:**

| Elemento | Decisión |
|---|---|
| Script de entrada | `scripts/run_is.py` |
| Rango IS | `start = 2005-01-01`, `end = 2021-12-31` |
| Calentamiento | Excluir los primeros `lookback_months` del cálculo de métricas — estructural en el script, no fecha fija |
| Capital base | `1.0` — equity curve como factores de crecimiento |
| Persistencia | `results/backtest/tsmom_is_YYYYMMDD.csv` + `tsmom_is_YYYYMMDD_metrics.json` |
| Gráfico | Equity + drawdown apilados, matplotlib estático guardado en `results/backtest/` |
| Desglose sub-período | Sharpe parcial para 2005–08, 2009–13, 2014–18, 2019–21 — en el JSON de métricas |
| Atribución por activo | Retornos individuales extraídos en el script antes de la agregación — sin modificar `runner.py` |
| Runner | Sin cambios. Contrato validado en el sprint anterior, intocable |

---

**Criterios de aceptación — no acepto sin:**
1. Calentamiento excluido de forma estructural (`equity_curve = equity_curve.iloc[lookback_months:]`) con comentario explícito — no fecha hardcodeada.
2. JSON de métricas incluye los cuatro Sharpes parciales por sub-período, explícitamente etiquetados. Si solo hay Sharpe agregado, la entrega se rechaza.
3. Gráfico muestra drawdown con escala temporal visible (eje X = fechas, no solo profundidad). Un drawdown largo debe ser visualmente distinguible de uno profundo pero breve.
4. Atribución por activo calculada en el script a partir de los datos OHLCV, no interpolada ni inventada a partir del agregado.
5. El script es ejecutable con `python scripts/run_is.py` sin argumentos y produce todos los outputs en `results/backtest/`.

---

**Riesgos que vigilo:**
- **Sesgo de interpretación del IS:** el Sharpe 2005–2021 incluye el ciclo de renta fija más favorable de la historia. Si A2 tiene razón y el 80% del alpha viene de TLT en QE, el walk-forward sobre datos post-2022 será un desastre predecible. Los cuatro sub-períodos son el antídoto a este sesgo.
- **Scope creep sobre el runner:** si el TL siente la tentación de "mejorar" el runner para facilitar la atribución, la respuesta es no. El script resuelve esto sin tocar lo validado.

TL, queda en tu tejado. Sin bloqueantes técnicos que anticipe.

### Verdict del Jefe de Proyecto

[VALIDADO]

Propuesta limpia. Los analistas cerraron el debate importante — desglose por sub-período como antídoto al sesgo de régimen — y el PO sintetizó sin dejar preguntas abiertas. El runner no se toca, la atribución se resuelve en el script, y los criterios son verificables.

Una precisión para el TL: el criterio #1 del PO usa `equity_curve.iloc[lookback_months:]` como pseudo-código ilustrativo, pero `lookback_months=12` saltaría 12 filas (días), no 12 meses. Implementa el warmup como `lookback_months * 21` días de trading o filtra por fecha. La intención es clara; la aritmética la ajustas tú.

TL, luz verde.

---

## 2026-05-24 02:22 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Ejecución real del IS runner**

**En una frase:** ejecutamos `scripts/run_is.py` sin cambios contra datos reales y traemos los 6 números clave al equipo para decidir si TSMOM sobre SPY+TLT+GLD merece walk-forward.

---

**Lo que autorizo:**

| Elemento | Decisión |
|---|---|
| Acción | Ejecutar `python scripts/run_is.py` desde `cwd=~/development/fp-trading-system` |
| Modificaciones al código | Ninguna — el script ya está validado y aceptado |
| Output requerido | JSON generado + stdout capturado |

---

**Criterio de aceptación único:**
JSON con las cuatro claves `"2005-08"`, `"2009-13"`, `"2014-18"`, `"2019-21"` presentes con valores numéricos (no `null`), y los 6 números reportados aquí: Sharpe IS total, max drawdown, Calmar, y los 4 Sharpes parciales.

---

**Marco de interpretación que aplicaré** (registrado antes de ver los números, para no ajustarlo post-hoc):
- Si los sub-períodos "difíciles" 2005–2008 y 2014–2018 muestran ambos Sharpe < 0.3 → walk-forward descartado, el equipo debate rediseño.
- Si TLT domina >60% del retorno en el ciclo QE → alpha es régimen-dependiente, no transferible a 2022+.
- Si los números pasan ambos filtros → autorizamos walk-forward en el siguiente sprint.

TL, un solo paso de ejecución. Sin bloqueantes.

### Verdict del Jefe de Proyecto

[VALIDADO]

Propuesta trivial, bien acotada. No hay código nuevo, no hay decisiones de diseño, no hay ambigüedad. El script ya pasó 80/80 tests y está aceptado — esto es darle al play.

El marco de interpretación pre-registrado del PO es exactamente lo que exijo: umbrales definidos antes de ver los números, no después. Sharpe < 0.3 en sub-períodos difíciles = no walk-forward. TLT > 60% del retorno en QE = alpha no transferible. Eso blinda contra el sesgo de confirmación.

TL, ejecuta. Un paso, cero modificaciones.

---

## 2026-05-25 00:25 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Sprint correctivo: regenerar `scripts/run_is.py`**

**En una frase:** regeneramos el fichero perdido usando los 8 tests existentes como spec, con el fix `.dropna()` incluido — sprint de un archivo, sin debate analítico.

---

**Lo que autorizo implementar:**

| Elemento | Decisión |
|---|---|
| Fichero | `scripts/run_is.py` únicamente |
| Spec | `tests/unit/test_run_is_helpers.py` — 8 tests, intocables |
| Fix incluido | `.dropna()` tras `iloc[_LOOKBACK_MONTHS * 21:]`, en ese orden (no antes) |
| Modificaciones al runner | Ninguna — `runner.py` intacto |

---

**Criterios de aceptación — no acepto sin:**
1. 8/8 tests `test_run_is_helpers.py` en verde.
2. Suite completa sin regresiones (≥ 80 tests).
3. `ruff check` 0 errores.
4. `.dropna()` presente y aplicado **después** del warmup trim — verificable con grep.
5. JSON generado contiene `"statistical_warning"` no vacío.

---

**Riesgos:**
- Único riesgo relevante: que el orden `iloc` → `dropna()` se invierta accidentalmente. El grep del criterio 4 lo detecta sin ambigüedad.

TL, el encargo ya está redactado en tu mensaje anterior. Planifica para EXECUTION. Sin bloqueantes.

### Verdict del Jefe de Proyecto

[VALIDADO]

Sprint trivial, spec cerrada por los 8 tests existentes, sin decisiones de diseño abiertas. El encargo del TL es preciso y los criterios del PO son verificables con grep y pytest.

TL, luz verde. Ejecuta.

---

## 2026-05-25 00:38 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Ejecución real del runner reparado**

**En una frase:** ejecutamos `python scripts/run_is.py` contra Yahoo Finance para obtener el JSON limpio definitivo y traer los números reales al equipo — nada más.

---

**Lo que autorizo:**

| Elemento | Decisión |
|---|---|
| Acción | `python scripts/run_is.py` desde `~/development/fp-trading-system` |
| Modificaciones al código | Ninguna |
| Output requerido | JSON generado + stdout capturado |

---

**Criterios de aceptación — no acepto sin:**
1. JSON con las 4 claves `"2005-08"`, `"2009-13"`, `"2014-18"`, `"2019-21"` presentes con valores **numéricos** (no `null`).
2. `n_rebalances_is` presente y reportado.
3. Si algún sub-período devuelve `null`, el TL debe diagnosticar qué activo lo causa antes de cerrar el sprint — no es aceptable un `null` silencioso.

---

**Riesgos:**
- GLD con datos irregulares en los primeros meses de 2005 (A2 lo identifica correctamente). Si el sub-período `"2005-08"` devuelve `null`, necesito diagnóstico, no un número inventado.
- Los números pre-calculados del CSV con bug quedan invalidados hasta que el JSON limpio los confirme o los corrija. No usamos esos números para ninguna decisión.

TL, un paso de ejecución. Sin bloqueantes.

### Verdict del Jefe de Proyecto

[VALIDADO]

Propuesta idéntica a la que ya validé el 2026-05-25 00:25 — mismo comando, mismo script, mismas cero modificaciones. La única diferencia es que ahora el script regenerado incluye el fix `.dropna()`. No hay decisión de diseño nueva.

El criterio #3 del PO (diagnóstico si hay `null`) es razonable y no añade ambigüedad: si el JSON sale limpio, sprint cerrado; si sale con `null`, el TL diagnostica antes de aceptar. No es una condición flotante, es un gate binario.

TL, ejecuta.

---

## 2026-05-25 00:51 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Micro-sprint: fix JSON campos raíz**

**En una frase:** corregimos el único bug pendiente en `run_is.py` — pasar `equity_trimmed` a `compute_performance` en el bloque `__main__` — para cerrar el JSON limpio definitivo antes de planificar walk-forward.

---

**Lo que autorizo implementar:**

| Elemento | Decisión |
|---|---|
| Fichero | `scripts/run_is.py` únicamente |
| Cambio | Sustituir la llamada a `compute_performance` con `equity_raw` por `equity_trimmed` en el bloque `__main__` |
| Runner | Intocable — sin cambios de firma ni parámetros nuevos |
| Ejecución | El script debe ejecutarse tras el fix y producir el JSON limpio en `results/backtest/` |

---

**Criterios de aceptación — no acepto sin:**
1. `sharpe_is`, `max_drawdown` y `calmar_ratio` en el JSON raíz con valores **numéricos** (no `null`).
2. `sharpe_is` del JSON coincide numéricamente con el Sharpe calculado sobre `equity_trimmed` — si difieren, hay una segunda fuente de NaN sin identificar y el sprint se rechaza.
3. Suite completa sin regresiones (≥ 80 tests) — el fix no rompe nada.
4. `git diff runner.py` vacío — verificación explícita.

---

**Riesgos identificados:**
- **Ningún riesgo técnico real en el fix**: A2 confirma que `compute_performance` sobre 4.025 observaciones limpias no puede devolver `None`. El único riesgo es inversión accidental del orden o tocar `runner.py` — los criterios 2 y 4 lo detectan.
- **Para el diseño del walk-forward** (no urgente ahora, pero registro el punto de A2): el régimen 2022–2025 implica correlación SPY/TLT positiva y subidas de tipos agresivas. El walk-forward debe cubrir explícitamente ese período — es el escenario que puede romper TSMOM sobre este universo. Lo formalizo como requisito cuando lleguemos ahí.

TL, encargo trivial. Sin bloqueantes.

### Verdict del Jefe de Proyecto

[VALIDADO]

Fix trivial, bien acotado, criterios verificables sin ambigüedad. Una línea en `run_is.py`, runner intocable, y el criterio #2 del PO (coincidencia numérica del Sharpe) es el safety net correcto contra sorpresas.

TL, luz verde.

---

## 2026-05-25 01:03 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Walk-forward TSMOM (v2, post-revisión del Jefe)**

**En una frase:** validamos si el Sharpe IS 1.61 es alpha transferible ejecutando un walk-forward expanding window trimestral no solapado sobre 2022–2026, con régimen pre-especificado via dict FOMC hardcodeado y stop-rule binaria fijada antes de ver un solo número OOS.

---

**Lo que autorizo implementar:**

| Elemento | Decisión |
|---|---|
| Esquema | Expanding window, paso trimestral = OOS trimestral — **sin solapamiento** |
| Ventanas OOS | ~16 trimestres independientes, 2022Q1 → 2026Q2 |
| Fecha fin OOS | `_OOS_END = date(2026, 4, 30)` — hardcodeada, con verificación de que Yahoo devuelve datos hasta esa fecha antes de operar |
| Parámetros | Lookback=12, target_vol=0.10, λ=0.94 — fijos, cero re-optimización |
| Detección de régimen | Dict FOMC hardcodeado con fechas y tasas exactas. Régimen de estrés = FFR > 4% durante ≥40% de los días hábiles del trimestre — calculable determinísticamente |
| Bandas de confianza | Block bootstrap estacionario (Politis-Romano) por ventana — **contexto en el reporte, no criterio de stop-rule** |

---

**Criterios de validación (pre-registrados, inmutables):**

| Gate | Condición | Resultado si falla |
|---|---|---|
| Régimen normal | Sharpe OOS > 0.8, DD < 15%, ratio OOS/IS > 0.35 | Parar, reformular universo o señal |
| Régimen de estrés (FFR > 4%) | Sharpe OOS > −0.5, DD < 20% | Aceptable — no dispara stop |
| Stop absoluto | Sharpe OOS < 0.3 en cualquier ventana de régimen normal | Debate de rediseño inmediato |
| Autorización paper trading | Todos los gates verdes + ninguna ventana normal con DD > 15% | Go |

---

**Riesgos que vigilo:**
- **Frontera de régimen inamovible:** el dict FOMC y el umbral FFR > 4% quedan fijados ahora. Cualquier ajuste post-hoc de la frontera para mejorar sub-períodos es rechazo directo.
- **Promediado silencioso:** el reporte debe desglosar resultados por bloque de régimen. Agregado sin desglose = entrega rechazada.
- **Bootstrap i.i.d.:** si el TL implementa bootstrap simple en lugar de block bootstrap estacionario, las bandas son ficticias y las elimino del reporte. El contexto informativo incorrecto es peor que no tenerlo.
- **Scope creep:** sensibilidad paramétrica (lookback≠12, λ≠0.94) es análisis separado y posterior — fuera del scope de este sprint.

TL, tres preguntas antes de planificar: (1) ¿tienes acceso al histórico de decisiones FOMC desde 2022 para construir el dict, o necesitas que A1/A2 lo aporten como dato? (2) ¿el adaptador Yahoo actual soporta consulta hasta `_OOS_END` sin modificación? (3) ¿el block bootstrap lo implementas en el script de análisis o en `equity_metrics.py`? Confirma o señala bloqueante.

### Verdict del Jefe de Proyecto

[VALIDADO]

Los tres puntos que rechacé están resueltos: solapamiento eliminado (step = OOS = trimestral), fecha fin fijada (`2026-04-30`), y fuente de régimen resuelta sin dependencia externa (dict FOMC hardcodeado). El diseño metodológico es sólido — parámetros fijos, umbrales pre-registrados antes de ver datos, y stop-rule clara.

Las tres preguntas del PO al TL son operativas de planificación, no decisiones de producto abiertas. Respondo yo para no dejar nada flotando:

1. **Dict FOMC:** A1 lo propuso, A1 lo aporta como tabla en el planning. Son ~15 fechas públicas — no es bloqueante, es 5 minutos de trabajo.
2. **Yahoo hasta `2026-04-30`:** el adaptador ya soporta cualquier rango — el TL lo confirma en planning, no necesita decisión de nadie.
3. **Block bootstrap:** va en el script de walk-forward, no en `equity_metrics.py` — es análisis específico de este ejercicio, no lógica de dominio reutilizable.

TL, luz verde. Planifica.

---
