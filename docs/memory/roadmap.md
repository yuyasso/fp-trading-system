# Roadmap

## En curso
(ninguna tarea en curso)

## Pendiente
(el PO poblará este listado cuando se valide la primera propuesta)

## Completado

- **2026-05-25 01:20** — Sprint aceptado · Sesión `301b0cfc` · ** Implementar `scripts/run_wf.py` — walk-forward expanding window trimestral sobre 2022Q1–2026Q1, con detección de régimen via dict FOMC hardcodeado, block bootstrap estacionario como contexto, y eva

- **2026-05-25 00:53** — Sprint aceptado · Sesión `088d4bce` · ** Corregir `scripts/run_is.py` para que los campos raíz `sharpe_is`, `max_drawdown` y `calmar_ratio` del JSON sean numéricos, pasando `equity_trimmed` a `compute_performance` en el bloque `__main__`,

- **2026-05-25 00:43** — Sprint aceptado · Sesión `c0d242d9` · ** Ejecutar `python scripts/run_is.py` contra Yahoo Finance, capturar el JSON generado y reportar los 6 números reales al equipo.

- **2026-05-25 00:29** — Sprint aceptado · Sesión `b128c4bf` · ** Recrear `scripts/run_is.py` (perdido por fallo de escritura) para que los 8 tests existentes pasen en verde, incluyendo el fix `.dropna()` que elimina los `null` del JSON de métricas.

- **2026-05-24 01:14** — Sprint completado · Sesión `d1b1b9b6` · No tengo permisos de lectura en este contexto de agente. Procedo con el encargo basándome en la estructura del repo y el historial — tengo suficiente información para ser preciso. Una nota importante 

- **2026-05-24 01:46** — Sprint completado · Sesión `770d5eb2` · **Objetivo:** Crear `scripts/run_is.py` que ejecuta el runner TSMOM IS (2005–2021), persiste equity curve, métricas con desglose por sub-período y atribución por activo, y genera gráfico equity+drawdo
