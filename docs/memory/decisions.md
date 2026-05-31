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

## 2026-05-25 01:45 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — TSMOM universo ampliado v2**

**En una frase:** mismo TSMOM long-only, mismos parámetros, pero sobre SPY+TLT+GLD+DBC+UUP — donde DBC y UUP aportan exposición a regímenes inflacionarios que el universo v1 no cubría.

---

**Lo que propongo implementar:**

| Elemento | Decisión |
|---|---|
| Estrategia | TSMOM long-only, lookback=12, λ=0.94, target_vol=0.10 — sin tocar señal ni runner |
| Universo v2 | SPY + TLT + GLD + **DBC** (desde ~2006) + **UUP** (desde ~2007) |
| NaN de UUP en 2005–2006 | Peso 0 automático por señal inválida — mecanismo existente, sin modificar runner |
| Long/short | Fuera de scope — diferido indefinidamente |
| Filtro de vol | Fuera de scope — diferido a v3 si v2 no es suficiente |
| Yield real | Contexto diagnóstico en el reporte, no parámetro |

---

**Criterios de validación (pre-registrados, inmutables):**

Mismos gates que v1 — no se renegocia porque el universo cambie:
- Régimen normal: Sharpe OOS > 0.8, DD < 15%, ratio OOS/IS > 0.35
- Régimen estrés: Sharpe OOS > −0.5, DD < 20%
- Stop absoluto: Sharpe OOS < 0.3 en cualquier ventana normal

**Check diagnóstico post-hoc** (no gate de aceptación, resuelvo el punto del Jefe): confirmar si DBC y/o UUP tenían señal positiva y peso > 0 en 2022Q2. Si ninguno tenía, el universo ampliado no resuelve el problema original y debatimos antes de aceptar los resultados.

**Precondición de planificación** (A2 lo señala, es razonable): verificar con datos Yahoo reales que UUP tiene retorno positivo a 12m entrando en enero 2022 — si no, UUP solo aporta base IS sin el impacto OOS que motiva su inclusión. El TL puede resolver esto con un one-liner antes de planificar el sprint completo.

---

**Riesgos que vigilo:**
- **Selección por comportamiento OOS:** DBC y UUP se incluyen por causalidad económica IS-first, no porque funcionaron en 2022. Si el walk-forward v2 pasa y alguien argumenta "claro, elegimos los que subieron en 2022", ese argumento es inválido — la justificación está documentada antes de ver los resultados.
- **IS truncado para UUP:** sub-período 2005-08 tiene ~1 año de datos de UUP. El Sharpe parcial 2005-08 del v2 no será directamente comparable con el v1 — hay que etiquetarlo explícitamente en el reporte.

Jefe, esta propuesta resuelve los dos puntos de rechazo: universo cerrado (5 tickers definidos) y criterio 2022Q2 reformulado como diagnóstico post-hoc. ¿Revisas?

### Verdict del Jefe de Proyecto

[VALIDADO]

Los dos puntos del rechazo anterior están resueltos: universo cerrado (5 tickers con justificación económica y datos ≥2007), y el criterio 2022Q2 degradado a diagnóstico post-hoc. No queda ninguna pregunta abierta ni condición flotante.

La precondición de A2 (verificar señal UUP en Q1 2022) es un one-liner que el TL resuelve en los primeros 30 segundos de planificación — no es una decisión pendiente, es una consulta de datos trivial.

TL, luz verde. Planifica.

---

## 2026-05-25 22:50 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Lectura tabla WF v2**

**En una frase:** ejecutamos un one-liner de lectura sobre el CSV ya generado para traer las 18 ventanas al canal y desbloquear el debate de rediseño.

---

**Lo que autorizo:**

| Elemento | Decisión |
|---|---|
| Acción | `python -c "..."` — lectura del último `*wf*.csv` + JSON summary |
| Modificaciones al repo | Ninguna — cero ficheros tocados |
| Output requerido | Tabla 18 ventanas con `quarter / regime / sharpe_is / sharpe_oos / max_dd_oos / gate_pass / stop_triggered` + `stop_quarter` si existe en el JSON |

---

**Criterio de aceptación único:**
Tabla completa de 18 ventanas reportada en el canal con todas las columnas listadas. Si hay menos de 18 filas o alguna columna falta, el TL diagnostica antes de cerrar.

**Riesgos:** ninguno — sprint de lectura pura sin consecuencias sobre el repo.

TL, el comando ya está redactado en tu mensaje anterior. Pásalo a Claude Code. Sin planificación adicional necesaria.

### Verdict del Jefe de Proyecto

[VALIDADO]

Sprint de lectura pura, cero riesgo, cero ambigüedad. El comando ya está escrito, el criterio es binario (tabla completa o diagnóstico), y no se toca ni un archivo del repo.

TL, ejecuta.

---

## 2026-05-25 23:03 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — TSMOM v3a: filtro de correlación SPY/TLT (primera iteración secuencial)**

**En una frase:** validamos un único overlay — filtro de correlación rolling SPY/TLT 60d con umbral P90 IS fijo — antes de introducir cualquier segunda capa.

---

**Lo que propongo implementar (v3a únicamente):**

| Elemento | Decisión |
|---|---|
| Señal base | TSMOM 12m, λ=0.94 — intocable |
| Overlay | Correlación rolling SPY/TLT 60d: si supera P90 IS 2005–2021 → target_vol al 50% (5%) |
| Calibración | Un único valor numérico fijo, calculado sobre IS completo 2005–2021, persistido como `"correlation_p90_threshold"` en el JSON — no rolling, no recalculado por ventana |
| Scope | v3b (dual-momentum) se diseña solo si v3a pasa gates. v3c (combinado) solo si ambos pasan por separado |
| Runner | Intocable. El overlay vive en el script de WF v3a — capa externa |

---

**Criterios de validación (pre-registrados, inmutables):**

| Gate | Condición | Resultado si falla |
|---|---|---|
| Anti-overfitting IS | Filtro dispara en ≥2 eventos IS distintos de 2022 (verificar Taper Tantrum 2013 y COVID 2020 explícitamente) | Umbral descartado — debate de nuevo umbral antes de WF |
| No daño COVID 2020 | Si filtro disparó en marzo 2020: cuantificar retorno perdido vs. drawdown evitado | Si retorno perdido > drawdown evitado, overlay rechazado |
| Exposición Q2+Q4 2022 | Exposición media ≥ 50% en los días hábiles de 2022Q2 y 2022Q4 con overlay activo | Si < 50%, la estrategia es timing de mercado, no TSMOM — rechazo directo |
| WF v3a 2022Q2 y Q4 | Sharpe OOS ≥ −0.5 en ambas ventanas | No pasa a v3b — debate de rediseño |
| Gates originales intactos | Régimen normal: Sharpe OOS > 0.8, DD < 15%; stop absoluto < 0.3 | Sin autorización de paper trading |

---

**Riesgos que vigilo:**
- **P90 cosmético:** si el valor calculado coincide numéricamente con el pico de 2022, el percentil no aporta nada sobre el valor absoluto. El count de disparos IS es la verificación que lo desmiente o confirma.
- **Scope creep hacia v3b antes de tiempo:** si v3a no resuelve 2022Q2/Q4, no arrancamos v3b hasta debatir si el problema es el overlay o la señal base.

Jefe, los tres puntos de rechazo anterior quedan cubiertos: gate de exposición formalizado (Q2+Q4, no año completo), testeo secuencial explícito, y percentil fijo con valor auditado. ¿Revisas?

### Verdict del Jefe de Proyecto

[VALIDADO]

Los tres puntos del rechazo anterior están resueltos con precisión:

1. **Gate de exposición formalizado:** ≥50% en Q2+Q4 2022 específicamente, no año completo. Criterio binario, medible, con consecuencia clara (rechazo si < 50%). La matización de A2 es correcta — medir el año completo penalizaría comportamiento deseable.

2. **Testeo secuencial:** v3a solo → v3b solo si v3a pasa → v3c solo si ambos pasan. Elimina la ambigüedad de atribución. Si v3a resuelve 2022Q2/Q4, v3b ni se toca.

3. **Percentil fijo:** un número, calculado sobre IS 2005–2021, persistido como campo auditable en el JSON. Sin rolling, sin recalculación por ventana. Limpio.

El gate anti-overfitting (≥2 disparos IS fuera de 2022) es la salvaguarda correcta contra el riesgo de calibrar sobre un evento único. Si solo dispara en 2022, el umbral muere antes de llegar al WF.

TL, luz verde. Planifica v3a.

---

## 2026-05-25 23:22 — Decisión validada por el Jefe

### Síntesis del Product Owner

Sprint de ejecución trivial — no hay debate que sintetizar. El script está aceptado, los gates pre-registrados son inmutables, y los 4 números que necesitamos son el output directo del script.

**Autorizo el sprint de ejecución.** Un solo paso, cero modificaciones al repo. El TL ejecuta `python scripts/run_wf_v3a.py` y trae al canal:

1. `correlation_p90_threshold` — si el valor es muy próximo al pico de 2022, el gate anti-overfitting se vuelve la verificación crítica.
2. `taper_tantrum_2013_fired` y `covid_2020_fired` — esto decide si el umbral es robusto o cosmético.
3. `exposure_2022q2` y `exposure_2022q4` — gate de exposición ≥50%.
4. Sharpe OOS de 2022Q2 y Q4 — gate WF ≥ −0.5.

**El veredicto lo emito yo cuando tenga los 4 números**, aplicando los criterios pre-registrados sin negociación. TL, adelante.

### Verdict del Jefe de Proyecto

[VALIDADO]

Sprint de ejecución pura, cero ambigüedad. El script está implementado, testeado (124 passed), y aceptado. Los gates son inmutables y pre-registrados. No hay decisión de diseño pendiente ni condición flotante.

TL, ejecuta y trae los números. El PO aplica los gates cuando los tenga.

---

## 2026-05-28 17:52 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Verificación de masa estadística antes de cualquier v3b**

**En una frase:** antes de diseñar cualquier overlay o cambiar el horizonte de señal, ejecutamos un one-liner IS que decide de forma binaria y pre-registrada qué camino tomamos.

---

**Lo que propongo:**

Un único sprint de diagnóstico, sin código nuevo en el repo principal. One-liner sobre los datos IS ya disponibles:

> Contar cuántos *ciclos de hiking independientes* (no trimestres individuales) en IS 2005–2021 cumplen simultáneamente: FFR trailing 6m >100bps **Y** retorno TSMOM negativo en ese trimestre.

**Criterio binario pre-registrado (inamovible antes de ver el resultado):**

| Resultado | Consecuencia |
|---|---|
| ≥ 3 ciclos independientes | Diseñamos overlay FFR ex-ante como v3b |
| < 3 ciclos independientes | Cerramos la línea de overlays. Debate único: ¿toleramos que TSMOM 12m/quarterly tenga este régimen de falla documentado (2 fallos en 18 ventanas, tail event de 450bps) como condición conocida de paper trading a tamaño reducido? |

La independencia es por ciclo, no por trimestre — condición de A2 incorporada.

---

**Riesgos que vigilo:**

- **Rama < 3:** la discusión sobre "¿relajamos el stop-rule?" no es técnica — es una decisión de hipótesis macro (¿esperamos más episodios inflacionarios en los próximos años?). Si llegamos a esa rama, **no procedo sin debate explícito del equipo sobre esa hipótesis antes de tocar ningún parámetro**.
- **Alcance del one-liner:** este diagnóstico no abre sprint de implementación. Trae un número. Ese número activa una de las dos ramas. Nada más.

TL, el encargo es un one-liner de lectura. Sin código nuevo, sin modificar el repo. ¿Algún bloqueante?

### Verdict del Jefe de Proyecto

[VALIDADO]

Propuesta limpia. Criterio binario pre-registrado antes de ver datos, independencia por ciclo (no trimestre), scope mínimo (un número, cero código nuevo), y ambas ramas con consecuencia definida.

Una precisión para el TL: la definición de "ciclo de hiking independiente" debe quedar operacionalizada antes de ejecutar. Propongo: dos trimestres pertenecen al mismo ciclo si no hay un trimestre intermedio donde FFR trailing 6m < 100bps. Si A1/A2 tienen otra definición, que la expliciten ahora — no después de ver el resultado.

TL, luz verde.

---

## 2026-05-28 18:04 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — Extensión `_FOMC_RATES` y re-diagnóstico IS**

**En una frase:** extendemos el dict FOMC con el historial 2004–2021 y re-ejecutamos el one-liner — un único fichero modificado, resultado empírico real que activa una de las dos ramas pre-registradas.

---

**Lo que autorizo:**

| Elemento | Decisión |
|---|---|
| Fichero | `scripts/run_wf.py` — únicamente el dict `_FOMC_RATES` |
| Entradas a añadir | ~35 decisiones FOMC (Greenspan 2004–2006, easing 2007–2008, ZLB 2009–2015 placeholder, Yellen 2015–2018, Powell 2019–2020) exactamente como el TL las lista |
| Lógica de clasificación | Intocable — sin modificar ninguna función |
| Tests | `test_run_wf_helpers.py` deben pasar sin regresiones — verificación obligatoria |

---

**Criterio de aceptación único:**

El resultado del one-liner es el número de ciclos independientes. Ese número activa una de las dos ramas pre-registradas — inamovibles — y eso es todo lo que este sprint produce. Si el número es ≥ 3, debatimos v3b. Si es < 3, cerramos la línea de overlays y el debate siguiente es sobre hipótesis macro y stop-rule.

---

**Riesgos que vigilo:**

- La entrada ZLB `2009-01-01` es un placeholder, no una decisión FOMC real. El TL debe marcarla con comentario explícito en el dict — A1 lo señala correctamente.
- La expectativa a priori de A1 (0–1 trimestres en la intersección) es razonable, pero el criterio binario pre-registrado no cambia según la expectativa. El número decide, no la intuición.

TL, el encargo está redactado y A1 ha dado el visto bueno. Procede a EXECUTION.

### Verdict del Jefe de Proyecto

[VALIDADO]

Sprint trivial, bien acotado. Un dict se extiende con datos públicos, se re-ejecuta un one-liner ya validado, y el resultado activa una rama pre-registrada e inamovible. No hay decisión de diseño abierta, no hay lógica nueva, no hay ambigüedad.

El comentario de A1 sobre el placeholder ZLB es pertinente — el PO ya lo recoge como requisito. Los criterios de aceptación del TL son verificables y suficientes.

TL, luz verde.

---

## 2026-05-28 18:19 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Respuesta a A2 sobre long-only:** es una decisión de scope que yo tomé en el sprint inicial para el MVP — no es una restricción explícita de Fran. Eso significa que puede revisarse, pero requiere un sprint de diseño separado con su propio debate. No lo abrimos aquí.

---

**Re-síntesis PO — Simulación forward v2 (versión final)**

**En una frase:** arrancamos simulación forward diaria con TSMOM v2 como proceso operativo de fase 0, sin capital comprometido, con el TL resolviendo la viabilidad técnica en PLANNING — no como prerequisito de esta síntesis.

---

**ADR — Excepción a gates (ya validada por el Jefe):**
Los dos fallos OOS son out-of-distribution. Fase 0 no requiere gates de paper trading — es simulación sin capital. La excepción autoriza arrancar el proceso operativo, no capital real.

**Lo que autorizo implementar:**

| Elemento | Decisión |
|---|---|
| Modo | Log de señales diario — sin broker, sin capital |
| Datos | Yahoo Finance cierre D-1, mismo adaptador existente |
| Duración mínima | 2 trimestres antes de evaluar fase 1 |
| Criterio de salida a fase 1 | Stop simulado −8% no disparado + capital confirmado por Fran |
| Long-only | Sigue siendo scope explícito — revisable en sprint separado posterior |

**Criterio de aceptación único del sprint:**
El TL entrega un script ejecutable diariamente que: (1) descarga precios cierre, (2) calcula señal TSMOM v2, (3) registra en CSV `{date, ticker, signal, weight}`. Si hay infraestructura nueva requerida, el TL lo notifica como bloqueante en PLANNING — no lo resuelvo yo en síntesis.

**El valor de fase 0** (incorporando punto de A2): documentar el proceso operativo y los pesos diarios reales, no confirmar que la estrategia funciona en régimen actual — eso ya lo sabemos. El aprendizaje es operativo, no estadístico.

**Jefe, esta síntesis es autocontenida.** La pregunta de infraestructura pasa al TL en PLANNING como primer punto del encargo — no como condición que bloquea la síntesis. ¿Revisas?

### Verdict del Jefe de Proyecto

[VALIDADO]

Propuesta autocontenida. El ADR de excepción a gates está resuelto y respaldado por A1. El scope es mínimo y honesto: log de señales sin capital, no paper trading. El criterio de aceptación es único y verificable. La pregunta de infraestructura pasa correctamente al TL en PLANNING como punto operativo, no como condición flotante de la síntesis.

El punto de A2 sobre el valor real de fase 0 (proceso operativo, no confirmación estadística) queda incorporado y es la lectura correcta. No estamos validando la estrategia — estamos construyendo el proceso que la ejecutaría.

TL, luz verde. En PLANNING tu primer punto es confirmar viabilidad técnica del cron diario sobre lo existente o notificar bloqueante. Segundo punto: planificar el script con el criterio de aceptación del PO.

---

## 2026-05-31 18:20 — Decisión validada por el Jefe

### Síntesis del Product Owner

**Síntesis PO — TSMOM long/short sobre SPY+TLT+GLD (v4)**

**En una frase:** rediseñamos TSMOM como long/short simétrico sobre el universo reducido SPY+TLT+GLD, con gate IS sub-período que exige mejora incremental real sobre long-only antes de tocar el OOS.

---

**Estrategia concreta propuesta:**

| Elemento | Decisión |
|---|---|
| Señal | TSMOM 12m, mismos parámetros — solo cambia la dirección |
| Universo | SPY + TLT + GLD — DBC y UUP **fuera** en ambas direcciones (coste de borrow adverso documentado por A2) |
| Dirección | Long/short simétrico — signal ∈ {+1, −1}, sin posición cero |
| Costes de borrow | Modelados explícitamente en IS: 0.1% anual TLT, 0.4% GLD, 0.0% SPY (estimaciones A2) |
| Universo v2 | Congelado — no se añaden activos hasta que este diseño pase o falle |

---

**Criterios de validación IS (pre-registrados, inamovibles):**

| Gate | Condición | Si falla |
|---|---|---|
| Mejora incremental sub-período | Sharpe L/S > Sharpe L-only en ≥3 de 4 sub-períodos, delta ≥ 0.15 | Debate cerrado — no pasamos a OOS |
| No concentración Greenspan | Alpha incremental de 2005–08 < 50% del total | Si > 50%, problema estructural — debate antes de OOS |
| IS ajustado por borrow | Sharpe L/S neto ≥ 1.4 | Sprint rechazado |

Gates OOS: los mismos del WF v2 (Sharpe > 0.8, DD < 15%, ratio OOS/IS > 0.35 en régimen normal).

---

**Riesgos que vigilo:**

- **Sesgo de selección declarado:** el ADR debe nombrar explícitamente que sabemos que 2022 falla long-only y que los shorts habrían funcionado ahí. La justificación metodológica (Moskowitz et al., motivación económica) mitiga pero no elimina ese sesgo — el gate de 4 sub-períodos es el antídoto.
- **Sharpe IS inflado por Greenspan:** si 2005–08 explica >50% del delta, el sistema tiene el mismo problema estructural que antes, solo con otra concentración. Es un gate, no una advertencia.
- **Complejidad operativa:** pasar a real con shorts requiere broker con API (Interactive Brokers o equivalente). No es decisión de este sprint, pero Fran debe saberlo como prerequisito de fase 1.

---

Jefe, esta síntesis incorpora todas las posiciones de A1 y A2 sin condiciones flotantes. ¿Revisas?

### Verdict del Jefe de Proyecto

[VALIDADO]

Propuesta cerrada y autocontenida. El TL puede planificar sin hacer preguntas adicionales.

Los tres gates IS son medibles, pre-registrados antes de ver datos, y con consecuencia definida si fallan. El sesgo de selección está nombrado en el ADR como exige A1. El universo está cerrado (3 activos, sin asimetría). Los costes de borrow son fijos y entran en el IS — no son un ajuste post-hoc.

Dos puntos que vigilo desde aquí:

1. **El gate de concentración Greenspan debe operacionalizarse sin ambigüedad en el plan del TL.** La métrica natural es: delta Sharpe del sub-período 2005-08 dividido por la suma de los 4 deltas sub-período. Si el TL lo interpreta de otra forma, que lo explicite en PLANNING antes de ejecutar.

2. **Signal ∈ {+1, −1} sin posición cero** es una decisión fuerte — siempre en mercado, sin estado neutral. Es coherente con TSMOM clásico y lo acepto, pero el equipo debe ser consciente de que elimina la opción de "salirse" cuando el momentum es ambiguo.

TL, luz verde. Planifica.

---
