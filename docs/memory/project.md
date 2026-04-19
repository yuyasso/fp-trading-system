# Proyecto: Sistema de Trading Rentable

## Misión
Construir un sistema de trading rentable, robusto y auditable, apto para operar en mercados reales tras validación exhaustiva en backtesting y simulación.

## Objetivos cuantitativos (a revisar por el equipo)
- Sharpe ratio out-of-sample > 0.8 (tras costes y slippage)
- Max drawdown < 15%
- Calmar ratio > 0.5
- Rentabilidad neta anualizada objetivo: a definir por el equipo

## Restricciones
- Sandbox: todo desarrollo ocurre contra datos históricos y simulación hasta que el humano (Fran) apruebe transición a capital real.
- Arquitectura hexagonal como default.
- TDD donde aplique (lógica de dominio, cálculos, reglas).
- Máster del repo protegida: todo cambio entra por MR.
- Rigor metodológico innegociable: walk-forward serio, out-of-sample respetado, cero data leakage.

## Principios rectores
- Nada de atajos que generen deuda técnica costosa.
- Métricas honestas: ningún backtest con Sharpe sospechosamente alto pasa sin inspección de overfitting.
- Decisiones documentadas en `decisions.md`.
- Estrategias descartadas quedan registradas en `strategies_tested.md` para no reintentarlas.

## Estado del proyecto
Arranque. Pendiente de definición de estrategia inicial.
