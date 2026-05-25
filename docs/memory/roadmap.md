# Roadmap

## En curso
(ninguna tarea en curso)

## Pendiente
(el PO poblará este listado cuando se valide la primera propuesta)

## Completado

- **2026-05-25 23:20** — Sprint aceptado · Sesión `16aa6245` · ** Implementar `scripts/run_wf_v3a.py` — walk-forward TSMOM con overlay de correlación rolling SPY/TLT 60d (target_vol × 0.5 cuando correlación supera P90 IS fijo), sin modificar runner.py, con gates

- **2026-05-25 22:55** — Sprint aceptado · Sesión `8ab49e33` · ** Leer el CSV del WF v2 e imprimir únicamente las filas que faltan (tail 5) y el JSON summary completo para cerrar el sprint de lectura.

- **2026-05-25 01:56** — Sprint aceptado · Sesión `c73bf0b5` · ** Ejecutar TSMOM walk-forward v2 sobre universo ampliado SPY+TLT+GLD+DBC+UUP, actualizando los scripts IS y WF con los nuevos tickers, añadiendo etiquetado de IS truncado para UUP, y el check diagnós

- **2026-05-25 01:20** — Sprint aceptado · Sesión `301b0cfc` · ** Implementar `scripts/run_wf.py` — walk-forward expanding window trimestral sobre 2022Q1–2026Q1, con detección de régimen via dict FOMC hardcodeado, block bootstrap estacionario como contexto, y eva

- **2026-05-25 00:53** — Sprint aceptado · Sesión `088d4bce` · ** Corregir `scripts/run_is.py` para que los campos raíz `sharpe_is`, `max_drawdown` y `calmar_ratio` del JSON sean numéricos, pasando `equity_trimmed` a `compute_performance` en el bloque `__main__`,

- **2026-05-25 00:43** — Sprint aceptado · Sesión `c0d242d9` · ** Ejecutar `python scripts/run_is.py` contra Yahoo Finance, capturar el JSON generado y reportar los 6 números reales al equipo.

- **2026-05-25 00:29** — Sprint aceptado · Sesión `b128c4bf` · ** Recrear `scripts/run_is.py` (perdido por fallo de escritura) para que los 8 tests existentes pasen en verde, incluyendo el fix `.dropna()` que elimina los `null` del JSON de métricas.

- **2026-05-24 01:14** — Sprint completado · Sesión `d1b1b9b6` · No tengo permisos de lectura en este contexto de agente. Procedo con el encargo basándome en la estructura del repo y el historial — tengo suficiente información para ser preciso. Una nota importante 

- **2026-05-24 01:46** — Sprint completado · Sesión `770d5eb2` · **Objetivo:** Crear `scripts/run_is.py` que ejecuta el runner TSMOM IS (2005–2021), persiste equity curve, métricas con desglose por sub-período y atribución por activo, y genera gráfico equity+drawdo
