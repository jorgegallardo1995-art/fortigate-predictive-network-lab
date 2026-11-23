# H10 — Auditoría de la ausencia neta de datos (base del Release)

`python -m scripts.auditoria_ausencia_datos --db snap.db`

## 1. Timestamps duplicados
- `timestamp_utc` duplicados: **0**
- `scheduled_timestamp` duplicados: **0**

## 2. Intervalos entre filas consecutivas (segundos)
- mediana=60.0 | p1=60.0 p5=60.0 p95=60.0 p99=90.0 | máximo=110530 (1842.2 min)
- intervalos irregulares (≠ 60 s ± 5 s): **1765** de 102519 (1.7 %); de ellos > 65 s (huecos): 1343, < 55 s: 422

## 3. Primera y última fila vs extremos del período
- primera fila: 2026-09-01 05:32:33.474885+00:00  (extremo período: 2026-09-01 05:32:33+00:00)
- última fila:  2026-11-14 23:59:46.578987+00:00  (extremo período: 2026-11-14 23:59:46+00:00)

## 4. Reconciliación de minutos
| concepto | valor | significado |
|---|---|---|
| minutos del período | 107,667 | (max−min de timestamp_utc) |
| filas crudas (telemetry_raw) | 102,520 | ciclos con una fila (exitosos + fallidos) |
| ciclos exitosos (collection_success=1) | 94,760 | filas con telemetría válida (las que usa el ML) |
| ciclos fallidos (collection_success=0) | 7,760 | fila presente, SIN telemetría válida |
| filas ausentes (minutos sin fila) | 5,147 | **minutos netos ausentes** = período − filas |
| collection_gaps: registros / lost_samples / minutos | 1046 / 13172 / 13,969 | **sobrecuenta** |

**Distinción de conceptos:**
- *Minutos sin NINGUNA fila* (ausencia física) = período − filas crudas = **5,147 min**. Es la cifra de «ausencia neta» (≈ 5.147).
- *Ciclos fallidos registrados* (fila presente, telemetría inválida) = **7,760**. Tienen fila, no son ausencia física, pero no aportan telemetría.
- *Minutos sin telemetría VÁLIDA* = (período − exitosos) = **12,907 min** (suma ausencia física + ciclos fallidos).
- `collection_gaps` (lost_samples 13172, 13,969 min) **sobrecuenta**: incluye los 7,760 ciclos fallidos del incidente de IP de la Raspberry contados como pérdida (y en parte dos veces). La ausencia física correcta es la de filas faltantes, no la de collection_gaps.
